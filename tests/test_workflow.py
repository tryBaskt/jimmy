import pytest
from paperlab.workflow import Workflow, Narrative, Cancelled
from paperlab.models import PaperPlan, Extensions
from paperlab.agents import verify_citations


class Agents:
    def call(self, *args, **kwargs):
        return Narrative(explanation="Evidence is limited.", limitations=["Modified sample."])

    def extend(self, *args):
        return Extensions(experiments=[], exclusions=["No viable child experiment"])


def seed(store):
    store.put("project#p", "meta", {"id": "p", "owner": "u"}, 0)
    store.put("project#p", "run#r", {"id": "r", "status": "queued", "paper_id": "pdf"}, 0)


def test_grounded_citations_only(experiment):
    plan = PaperPlan(
        title="P",
        main_claim="C",
        supporting_claims=[],
        citations=[{"page": 1, "quote": "winners"}],
        experiments=[experiment],
    )
    verify_citations(plan, [{"page": 1, "text": "Past winners outperform"}])
    with pytest.raises(ValueError):
        verify_citations(plan, [{"page": 1, "text": "Different text"}])


def test_stage_idempotence_and_frozen_inputs(settings, store, experiment):
    seed(store)
    w = Workflow(settings, store, "p", "r", agents=Agents())
    w.save(
        "prepared",
        [
            {
                "experiment": experiment.model_dump(mode="json"),
                "status": "blocked",
                "group": "paper_experiment",
                "reason": "missing",
            }
        ],
    )
    w.stage("freeze")
    first = w.load("frozen")
    w.stage("freeze")
    assert w.load("frozen") == first
    first["experiments"][0]["reason"] = "tampered"
    w.save("frozen", first)
    with pytest.raises(ValueError, match="integrity"):
        w.run_group("frozen", "results")


def test_two_groups_report_after_recorded_extension(settings, store, experiment):
    seed(store)
    w = Workflow(settings, store, "p", "r", agents=Agents())
    w.save("plan", {"main_claim": "C"})
    w.save(
        "compared",
        [{"experiment": experiment.model_dump(mode="json"), "status": "blocked", "reason": "missing"}],
    )
    w.extend()
    w.save("extension_evaluated", [])
    w.report()
    report = w.load("report")
    assert report["claim_assessment"] == "inconclusive"
    assert report["coverage"]["central_missing"] == ["momentum"]
    assert report["extrapolated_experiments"] == []
    assert "adapted_experiments" not in report


def test_cancel_is_honored(settings, store):
    seed(store)
    row, rev = store.get("project#p", "run#r")
    row["status"] = "cancelled"
    store.put("project#p", "run#r", row, rev)
    with pytest.raises(Cancelled):
        Workflow(settings, store, "p", "r").stage("freeze")


def test_full_experiment_and_child_pipeline(settings, store, experiment):
    import pandas as pd
    from paperlab.models import Extension, Extensions, Selection

    seed(store)
    rows = []
    for i, date in enumerate(pd.date_range("2019-12-31", periods=11, freq="ME")):
        rows.extend(
            [
                {"date": str(date.date()), "security_id": "A", "return": 0.03 + i * 0.001},
                {"date": str(date.date()), "security_id": "B", "return": -0.01 + i * 0.0001},
            ]
        )
    body = pd.DataFrame(rows).to_csv(index=False).encode()
    store.blob_put("projects/p/input", body)
    store.put(
        "project#p",
        "upload#csv",
        {
            "id": "csv",
            "name": "returns.csv",
            "kind": "data",
            "status": "completed",
            "key": "projects/p/input",
        },
    )

    class FakeAgents(Agents):
        def select(self, exp, profiles, attempt):
            uploaded = next(x for x in profiles if x["connector"] == "upload")
            return Selection(
                candidate_id=uploaded["candidate_id"],
                rationale="Use uploaded returns",
                modified_experiment=None,
                caveats=["Historical universe not independently verified"],
                comparison_eligible=False,
            )

        def extend(self, executions, plan):
            child = experiment.model_copy(deep=True)
            child.id = "child"
            child.holding_months = 2
            return Extensions(
                experiments=[
                    Extension(parent_id=experiment.id, rationale="Holding sensitivity", experiment=child)
                ],
                exclusions=[],
            )

    class NoInternet:
        def discover(self, *args):
            return [], []

    w = Workflow(settings, store, "p", "r", agents=FakeAgents(), registry=NoInternet())
    plan = PaperPlan(
        title="P",
        main_claim="C",
        supporting_claims=[],
        citations=[{"page": 1, "quote": "winners"}],
        experiments=[experiment],
    )
    w.save("plan", plan.model_dump(mode="json"))
    for stage in [
        "data",
        "freeze",
        "build",
        "run",
        "compare",
        "extend",
        "extension_data",
        "extension_freeze",
        "extension_build",
        "extension_run",
        "extension_evaluate",
        "report",
    ]:
        w.stage(stage)
    result = w.load("report")
    assert result["paper_experiments"][0]["status"] == "completed"
    assert result["paper_experiments"][0]["mode"] == "modified"
    assert result["extrapolated_experiments"][0]["status"] == "completed"
    assert result["extrapolated_experiments"][0]["parent"] == "momentum"
    assert store.get("project#p", "run#r")[0]["status"] == "completed"
