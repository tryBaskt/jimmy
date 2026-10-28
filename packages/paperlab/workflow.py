"""Stage-addressable durable workflow, shared by local development and ECS tasks."""

from __future__ import annotations
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
import json
import os
import time
import uuid
from pydantic import Field
from pypdf import PdfReader
from paperlab.agents import BedrockAgents, verify_citations
from paperlab.catalog import Catalog, AcquisitionPending
from paperlab.connectors import Candidate, Registry
from paperlab.connectors.bigdata import BigdataConnector
from paperlab.connectors.yahoo import YahooConnector
from paperlab.models import Contract, Experiment, PaperPlan
from paperlab.numerics import execute, compare, validate_returns, TEMPLATE_VERSION
from paperlab.storage import Conflict, artifact, digest, emit

STAGES = [
    "ingest",
    "plan",
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
]


class Cancelled(Exception):
    pass


class Narrative(Contract):
    explanation: str = Field(max_length=10000)
    limitations: list[str]


class Workflow:
    def __init__(self, settings, store, project, run, agents=None, registry=None):
        self.settings, self.store, self.project, self.run_id = settings, store, project, run
        self.pk = f"project#{project}"
        self.agents = agents
        # Cross-user reuse requires explicit provider entitlement authorization at deployment.
        yahoo_scope = (
            "public:yfinance"
            if os.getenv("YFINANCE_SHARED_CACHE") == "true"
            else f"project:{project}:yfinance"
        )
        self.registry = registry or Registry(
            [
                YahooConnector(yahoo_scope),
                BigdataConnector(os.getenv("BIGDATA_API_KEY", ""), f"project:{project}:bigdata"),
            ]
        )

    def notify(self, kind, summary, detail=None, experiment_id=None, provisional=False):
        self.check_cancelled()
        return emit(
            self.store,
            self.project,
            kind,
            summary,
            run_id=self.run_id,
            experiment_id=experiment_id,
            detail=detail,
            provisional=provisional,
        )

    def check_cancelled(self):
        record, _ = self.store.get(self.pk, f"run#{self.run_id}")
        if not record or record.get("status") == "cancelled":
            raise Cancelled()

    def get(self, name):
        value, _ = self.store.get(self.pk, f"run#{self.run_id}#{name}")
        return value

    def save(self, name, value):
        key = artifact(self.store, self.project, self.run_id, name, value)
        self.store.put(self.pk, f"run#{self.run_id}#{name}", {"artifact": key})
        return key

    def load(self, name):
        ref = self.get(name)
        return json.loads(self.store.blob_get(ref["artifact"])) if ref else None

    def agent(self):
        if self.agents is None:
            self.agents = BedrockAgents(self.settings, self.store, self.project, self.run_id, self.notify)
        return self.agents

    def stage(self, name):
        if name not in STAGES:
            raise ValueError("Unknown workflow stage")
        marker = f"run#{self.run_id}#stage#{name}"
        previous, rev = self.store.get(self.pk, marker)
        if previous and previous["status"] == "completed":
            return
        if previous and previous.get("lease_until", 0) > time.time():
            raise AcquisitionPending("Stage is already executing")
        token = uuid.uuid4().hex
        self.store.put(
            self.pk, marker, {"status": "running", "token": token, "lease_until": time.time() + 1800}, rev
        )
        self.check_cancelled()
        record, run_rev = self.store.get(self.pk, f"run#{self.run_id}")
        if record.get("status") in {"failed", "completed"}:
            raise ValueError("Run is terminal")
        record.update(status="running", stage=name, updated_at=time.time())
        self.store.put(self.pk, f"run#{self.run_id}", record, run_rev)
        try:
            self.notify("stage_started", f"Started {name}")
            getattr(self, name)()
            self.check_cancelled()
            current, stage_rev = self.store.get(self.pk, marker)
            if current.get("token") != token:
                raise Conflict("Stage ownership changed")
            self.store.put(self.pk, marker, {"status": "completed", "token": token}, stage_rev)
            self.notify("stage_completed", f"Completed {name}")
            if name == "report":
                record, run_rev = self.store.get(self.pk, f"run#{self.run_id}")
                record.update(status="completed", stage="completed", updated_at=time.time())
                self.store.put(self.pk, f"run#{self.run_id}", record, run_rev)
        except Exception as exc:
            current, stage_rev = self.store.get(self.pk, marker)
            if current and current.get("token") == token:
                self.store.put(
                    self.pk,
                    marker,
                    {"status": "failed", "token": token, "error_type": type(exc).__name__},
                    stage_rev,
                )
            record, run_rev = self.store.get(self.pk, f"run#{self.run_id}")
            if record and record["status"] != "cancelled":
                record.update(
                    status="failed", error=f"{name} failed ({type(exc).__name__})", updated_at=time.time()
                )
                self.store.put(self.pk, f"run#{self.run_id}", record, run_rev)
                emit(self.store, self.project, "stage_failed", record["error"], run_id=self.run_id)
            raise

    def ingest(self):
        record, _ = self.store.get(self.pk, f"run#{self.run_id}")
        upload, _ = self.store.get(self.pk, f"upload#{record['paper_id']}")
        if not upload or upload.get("status") != "completed" or upload["kind"] != "paper":
            raise ValueError("A completed PDF upload is required")
        raw = self.store.blob_get(upload["key"])
        if digest(raw) != upload["checksum"]:
            raise ValueError("Paper checksum mismatch")
        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted or len(reader.pages) > 200:
            raise ValueError("Encrypted or oversized PDF")
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append({"page": i + 1, "text": text})
            self.notify(
                "page_extracted",
                f"Extracted page {i + 1} of {len(reader.pages)}",
                {"page": i + 1, "characters": len(text)},
            )
        if sum(len(p["text"]) for p in pages) > 500000:
            raise ValueError("Document text exceeds analysis budget")
        parser = "pypdf"
        if any(len(p["text"].strip()) < 30 for p in pages):
            if self.settings.mode != "aws":
                raise ValueError("Scanned PDF requires the AWS Textract deployment")
            from paperlab.ocr import extract

            self.notify("ocr_started", "Scanned or sparse pages detected; starting Textract")
            pages = extract(self.settings, upload, self.notify)
            parser = "textract"
        if sum(len(p["text"].strip()) for p in pages) < 100:
            raise ValueError("Insufficient readable content after extraction")
        self.save("extraction", {"pages": pages, "checksum": digest(raw), "parser": parser})

    def plan(self):
        pages = self.load("extraction")["pages"]
        plan = self.agent().plan(pages)
        verify_citations(plan, pages)
        self.save("plan", plan.model_dump(mode="json"))
        self.notify(
            "plan_available",
            "Main claim and supporting experiment suite identified",
            {
                "main_claim": plan.main_claim,
                "experiments": [e.model_dump(mode="json") for e in plan.experiments],
            },
            provisional=True,
        )

    def data(self):
        plan = PaperPlan.model_validate(self.load("plan"))
        self.prepare_data(plan.experiments, "prepared", "paper_experiment")

    def prepare_data(self, experiments, output, group, parents=None):
        prepared = []
        for original in experiments:
            self.check_cancelled()
            if original.method == "unsupported":
                prepared.append(
                    {
                        "experiment": original.model_dump(mode="json"),
                        "status": "blocked",
                        "reason": "Method outside supported numerical templates",
                        "group": group,
                    }
                )
                continue
            selected = None
            current = original
            seen = set()
            candidates_seen = []
            for attempt in range(self.settings.max_replans):
                candidates, errors = self.registry.discover(current.requirement, self.project)
                candidates = [c for c in candidates if c.id not in seen][:4]
                # Private uploaded numerical data are explicit candidates, never promoted to global scope.
                if attempt == 0:
                    for _, uploaded in self.store.query(self.pk, "upload#", limit=100):
                        if uploaded.get("status") == "completed" and uploaded["kind"] == "data":
                            candidates.append(
                                Candidate(
                                    uploaded["id"],
                                    "upload",
                                    uploaded["name"],
                                    {"key": uploaded["key"]},
                                    f"project:{self.project}",
                                    "stock_returns",
                                    [
                                        "Uploaded data require validation against the original universe and availability conventions."
                                    ],
                                )
                            )
                if not candidates:
                    break
                profiles = []
                for candidate in candidates[:5]:
                    seen.add(candidate.id)
                    self.notify(
                        "data_candidate", f"Evaluating {candidate.title}", asdict(candidate), original.id
                    )
                    try:

                        def fetch(c):
                            if c.connector == "upload":
                                return self.store.blob_get(c.request["key"]), {"source": "private_upload"}
                            return self.registry.fetch(c)

                        catalog = Catalog(
                            self.store, lambda k, d: self.notify(k, k.replace("_", " "), d, original.id)
                        )
                        wait_until = time.monotonic() + 60
                        while True:
                            try:
                                dataset = catalog.acquire(candidate, fetch)
                                break
                            except AcquisitionPending:
                                if time.monotonic() >= wait_until:
                                    raise ValueError(
                                        "Shared acquisition did not finish within waiting budget"
                                    )
                                self.notify(
                                    "acquisition_wait",
                                    "Waiting for an existing dataset acquisition",
                                    experiment_id=original.id,
                                )
                                time.sleep(3)
                        profile = {"candidate_id": candidate.id, **asdict(candidate), "dataset": dataset}
                        if candidate.kind == "stock_returns":
                            frame = validate_returns(self.store.blob_get(dataset["key"]))
                            profile["quality"] = {
                                "rows": len(frame),
                                "securities": frame.security_id.nunique(),
                                "start": str(frame.date.min().date()),
                                "end": str(frame.date.max().date()),
                                "frequency": "monthly",
                                "missing_held_returns": "checked at execution",
                            }
                        profiles.append(profile)
                    except AcquisitionPending:
                        raise
                    except (ValueError, OSError, RuntimeError) as exc:
                        errors.append({"candidate_id": candidate.id, "error": type(exc).__name__})
                candidates_seen.extend(profiles)
                if not profiles:
                    break
                selection = self.agent().select(current.model_dump(mode="json"), profiles, attempt)
                self.save(
                    f"selection-{original.id}-{attempt}",
                    {"profiles": profiles, "errors": errors, "selection": selection.model_dump(mode="json")},
                )
                chosen = next((p for p in profiles if p["candidate_id"] == selection.candidate_id), None)
                if chosen and chosen["kind"] == "stock_returns":
                    updated = selection.modified_experiment or current
                    if updated.id != original.id or updated.claim != original.claim:
                        raise ValueError("Replanning cannot replace the paper experiment's identity or claim")
                    modified = updated.model_dump(mode="json") != original.model_dump(mode="json")
                    # Yahoo is always qualified; no agent can override its historical universe limitations.
                    caveats = list(dict.fromkeys(original.caveats + selection.caveats + chosen["caveats"]))
                    qualified = modified or bool(caveats) or chosen["connector"] in {"yfinance", "upload"}
                    # Preserve original published targets; data substitutions cannot rewrite paper evidence.
                    updated.expected = original.expected
                    updated.citations = original.citations
                    updated.caveats = caveats
                    selected = {
                        "original": original.model_dump(mode="json"),
                        "experiment": updated.model_dump(mode="json"),
                        "manifest": chosen["dataset"],
                        "group": group,
                        "status": "ready",
                        "mode": "modified" if qualified else "original",
                        "comparison_eligible": selection.comparison_eligible and not qualified,
                        "rationale": selection.rationale,
                        "parent": (parents or {}).get(original.id),
                    }
                    self.notify("data_selected", selection.rationale, selected, original.id)
                    break
                if selection.modified_experiment is None:
                    break
                current = selection.modified_experiment
                if current.id != original.id or current.claim != original.claim:
                    raise ValueError("Invalid replan identity")
            prepared.append(
                selected
                or {
                    "experiment": original.model_dump(mode="json"),
                    "status": "blocked",
                    "group": group,
                    "reason": "No defensible executable numerical dataset within search budget",
                    "candidates_considered": len(candidates_seen),
                }
            )
        self.save(output, prepared)

    def freeze_group(self, prepared_name, frozen_name):
        prepared = self.load(prepared_name) or []
        frozen = {"experiments": prepared, "hash": digest(prepared), "frozen_at": time.time()}
        self.save(frozen_name, frozen)
        for item in prepared:
            if item.get("manifest"):
                manifest = item["manifest"]
                self.store.put(
                    f"references#{manifest['checksum']}",
                    f"project#{self.project}#run#{self.run_id}#experiment#{item['experiment']['id']}",
                    {
                        "project": self.project,
                        "run": self.run_id,
                        "key": manifest["key"],
                        "version": manifest["version"],
                        "purpose": prepared_name,
                    },
                )
        self.notify(
            "specification_frozen",
            f"Frozen {len(prepared)} experiment specifications",
            {"hash": frozen["hash"], "group": prepared_name},
        )

    def freeze(self):
        self.freeze_group("prepared", "frozen")

    def build_group(self, frozen_name, output):
        # Executable code is the reviewed package template, not arbitrary generated Python.
        code = Path(__file__).with_name("numerics.py").read_text()
        key = artifact(
            self.store,
            self.project,
            self.run_id,
            "implementation",
            {"source": code, "template": TEMPLATE_VERSION, "hash": digest(code.encode())},
        )
        self.save(
            output,
            {
                "implementation": key,
                "template": TEMPLATE_VERSION,
                "specification_hash": self.load(frozen_name)["hash"],
            },
        )

    def build(self):
        self.build_group("frozen", "implementation")

    def run_group(self, frozen_name, output):
        frozen = self.load(frozen_name)
        if frozen["hash"] != digest(frozen["experiments"]):
            raise ValueError("Frozen specification integrity failure")
        pending = list(frozen["experiments"])
        done = {}
        while pending:
            progress = False
            for item in list(pending):
                self.check_cancelled()
                exp = Experiment.model_validate(item["experiment"])
                ids = {Experiment.model_validate(x["experiment"]).id for x in pending}
                if any(dep in ids for dep in exp.dependencies):
                    continue
                pending.remove(item)
                progress = True
                bad_dep = any(
                    dep not in done or done[dep]["status"] != "completed" for dep in exp.dependencies
                )
                if item["status"] != "ready" or bad_dep:
                    result = {
                        **item,
                        "status": "blocked",
                        "reason": item.get("reason", "Unmet experiment dependency"),
                    }
                else:
                    self.notify("experiment_started", exp.title, {"group": item["group"]}, exp.id)
                    try:
                        body = self.store.blob_get(item["manifest"]["key"])
                        if digest(body) != item["manifest"]["checksum"]:
                            raise ValueError("Dataset checksum mismatch")
                        calculated = execute(body, exp)
                        result_key = artifact(
                            self.store, self.project, self.run_id, f"result-{exp.id}", calculated
                        )
                        wealth, curve = 1.0, []
                        points = calculated["series"]
                        stride = max(1, (len(points) + 179) // 180)
                        for point_index, point in enumerate(points):
                            wealth *= 1 + point["return"]
                            if point_index % stride == 0 or point_index == len(points) - 1:
                                curve.append({"date": point["date"], "value": wealth})
                        result = {
                            **item,
                            "curve": curve,
                            "status": "completed",
                            "result": result_key,
                            "metrics": calculated["metrics"],
                            "sample": calculated["sample"],
                        }
                        self.notify(
                            "experiment_result", f"Completed {exp.title}", result, exp.id, provisional=True
                        )
                    except ValueError as exc:
                        result = {**item, "status": "blocked", "reason": str(exc)}
                        self.notify("experiment_blocked", str(exc), {"experiment": exp.id}, exp.id)
                done[exp.id] = result
            if not progress:
                raise ValueError("Unresolved execution dependencies")
        self.save(output, list(done.values()))

    def run(self):
        self.run_group("frozen", "results")

    def compare(self):
        results = self.load("results")
        for item in results:
            if item["status"] == "completed":
                exp = Experiment.model_validate(item["experiment"])
                data = json.loads(self.store.blob_get(item["result"]))
                item["comparisons"] = compare(data, exp, item["comparison_eligible"])
                self.notify("comparison_available", f"Compared {exp.title}", item["comparisons"], exp.id)
        self.save("compared", results)

    def extend(self):
        usable = [x for x in self.load("compared") if x["status"] == "completed"]
        if not usable:
            self.save("extensions", {"experiments": [], "exclusions": ["No usable parent executions"]})
            self.notify(
                "extrapolation_unavailable", "No usable parent execution; extrapolation stage recorded"
            )
            return
        extensions = self.agent().extend(usable, self.load("plan"))
        parents = {x["experiment"]["id"]: x for x in usable}
        ids = set()
        for ext in extensions.experiments:
            if ext.parent_id not in parents or ext.experiment.id in ids:
                raise ValueError("Invalid extrapolation parent or duplicate ID")
            ids.add(ext.experiment.id)
            # Every proposal is result-driven; do not mislabel it untouched holdout validation.
            ext.designation = "exploratory"
            ext.experiment.expected = []
            ext.experiment.dependencies = []
            ext.experiment.caveats = list(
                dict.fromkeys(
                    ext.experiment.caveats
                    + parents[ext.parent_id]["experiment"]["caveats"]
                    + ["Result-driven exploratory extrapolation."]
                )
            )
        self.save("extensions", extensions.model_dump(mode="json"))
        self.notify(
            "extrapolation_plan",
            "Extrapolation suite planned",
            extensions.model_dump(mode="json"),
            provisional=True,
        )

    def extension_data(self):
        extensions = self.load("extensions")
        self.prepare_data(
            [Experiment.model_validate(x["experiment"]) for x in extensions["experiments"]],
            "extension_prepared",
            "extrapolated_experiment",
            {x["experiment"]["id"]: x["parent_id"] for x in extensions["experiments"]},
        )

    def extension_freeze(self):
        self.freeze_group("extension_prepared", "extension_frozen")

    def extension_build(self):
        self.build_group("extension_frozen", "extension_implementation")

    def extension_run(self):
        self.run_group("extension_frozen", "extension_results")

    def extension_evaluate(self):
        parents = {x["experiment"]["id"]: x for x in self.load("compared")}
        results = self.load("extension_results") or []
        for item in results:
            if item["status"] == "completed":
                parent = parents[item["parent"]]
                item["evaluation"] = {
                    "designation": "exploratory",
                    "parent": item["parent"],
                    "mean_return_difference": item["metrics"]["mean_monthly_return"]
                    - parent["metrics"]["mean_monthly_return"],
                    "caveat": "Descriptive comparison, not an independent significance test or causal result.",
                }
                self.notify(
                    "extension_evaluated",
                    item["experiment"]["title"],
                    item,
                    item["experiment"]["id"],
                    provisional=True,
                )
        self.save("extension_evaluated", results)

    def report(self):
        if self.load("extensions") is None or self.load("extension_evaluated") is None:
            raise ValueError(
                "Extrapolation stage must finish or record its limitations before final reporting"
            )
        paper = self.load("compared") or []
        extended = self.load("extension_evaluated") or []
        central_missing = [
            x["experiment"]["id"] for x in paper if x["experiment"]["central"] and x["status"] != "completed"
        ]
        report = {
            "main_claim": self.load("plan")["main_claim"],
            "paper_experiments": paper,
            "extrapolated_experiments": extended,
            "extrapolation_plan": self.load("extensions"),
            "coverage": {
                "required": len(paper),
                "completed": sum(x["status"] == "completed" for x in paper),
                "central_missing": central_missing,
            },
            "claim_assessment": "inconclusive",
            "assessment_rationale": "The report presents measured findings and coverage; successful numerical execution alone does not establish the full scientific claim.",
            "limitations": [
                "Computational reproduction does not establish causality or future profitability.",
                "Substituted data and methodology changes limit original-result comparisons.",
                "Only trusted monthly return templates are executable in this release.",
            ],
        }
        narrative = self.agent().call(
            "Explain ONLY the supplied measured evidence in plain language. "
            "Distinguish modified paper experiments from extrapolations, identify missing central evidence, "
            "do not declare the paper verified, and do not alter numerical findings.",
            report,
            Narrative,
            worker=True,
        )
        report["explanation"] = narrative.explanation
        report["limitations"].extend(narrative.limitations)
        self.save("report", report)
        self.notify("report_available", "Final assessment and research artifacts available", report)


def local_run(settings, store, project, run):
    workflow = Workflow(settings, store, project, run)
    for stage in STAGES:
        workflow.stage(stage)


def main():
    from paperlab.config import Settings
    from paperlab.storage import make_store

    settings = Settings.load()
    workflow = Workflow(settings, make_store(settings), os.environ["PROJECT_ID"], os.environ["RUN_ID"])
    workflow.stage(os.environ["STAGE"])


if __name__ == "__main__":
    main()
