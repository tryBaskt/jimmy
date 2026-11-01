"""Paper replication: plan -> collect/research -> review/retry -> parallel experiments -> report."""

import argparse
import json
import operator
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents import ResearchAgents, read_paper
from backtesting import run_experiment
from models import DatasetSpec, ExperimentSpec, ResearchPlan
from market_data import acquire, quality_check
from reporting import render_report, save_json


class ResearchState(TypedDict, total=False):
    paper_path: str
    plan: dict
    dataset: dict
    attempt: int
    acquisition: dict
    acquisitions: Annotated[list, operator.add]
    quality: dict
    web: dict
    web_history: Annotated[list, operator.add]
    decision: dict
    decisions: Annotated[list, operator.add]
    experiments: list
    experiment: dict
    results: Annotated[list, operator.add]
    warnings: Annotated[list, operator.add]
    report_path: str


@dataclass
class Runtime:
    agents: ResearchAgents
    run_dir: Path
    today: str
    overrides: dict
    max_data_attempts: int = 3
    max_extrapolations: int = 3
    web_search: bool = False
    local_csv: Path | None = None

    def event(self, stage, message):
        print(f"[{stage}] {message}", flush=True)
        with (self.run_dir / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                     "stage": stage, "message": message}) + "\n")


def create_workflow(runtime: Runtime):
    def planner(state):
        runtime.event("planner", "Reading paper and extracting the claim, methodology and published results")
        pages = read_paper(Path(state["paper_path"]))
        plan = runtime.agents.plan(pages, runtime.today, runtime.overrides)
        dataset = apply_overrides(plan.dataset, runtime.overrides)
        plan = plan.model_copy(update={"dataset": dataset})
        save_json(runtime.run_dir / "plan.json", plan.model_dump(mode="json"))
        (runtime.run_dir / "paper_text.txt").write_text(
            "\n\n".join(f"[PDF PAGE {i + 1}]\n{p}" for i, p in enumerate(pages)))
        return {"plan": plan.model_dump(mode="json"), "dataset": dataset.model_dump(mode="json"), "attempt": 1}

    def collect_prices(state):
        attempt = state["attempt"]
        runtime.event("data", f"Fetching dataset, attempt {attempt}/{runtime.max_data_attempts}")
        spec = DatasetSpec.model_validate(state["dataset"])
        folder = runtime.run_dir / f"data_attempt_{attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        acquired = acquire(spec, folder, runtime.local_csv)
        plan = ResearchPlan.model_validate(state["plan"])
        quality = quality_check(acquired, plan.experiments)
        save_json(runtime.run_dir / f"data_attempt_{attempt}" / "manifest.json", acquired)
        runtime.event("data", f"Availability={acquired['verified_available']}; usable={quality['usable']}")
        return {"acquisition": acquired, "acquisitions": [acquired], "quality": quality}

    def research_sources(state):
        if not runtime.web_search:
            web = {"status": "disabled", "notes": "Web discovery disabled by CLI.", "sources": {}}
        else:
            runtime.event("web", "Searching for original data, methodology and available alternatives")
            try:
                web = runtime.agents.search(ResearchPlan.model_validate(state["plan"]),
                                            state.get("acquisition"), state["attempt"])
            except Exception as error:
                web = {"status": "failed", "notes": f"Web discovery failed: {error}", "sources": {}}
        save_json(runtime.run_dir / f"web_research_{state['attempt']}.json", web)
        return {"web": web, "web_history": [web]}

    def review_data(state):
        runtime.event("planner", "Reviewing downloaded coverage and deciding whether a substitute is needed")
        warnings = []
        try:
            decision = runtime.agents.review_data(
                ResearchPlan.model_validate(state["plan"]), state["acquisition"], state["quality"],
                state["web"], state.get("decisions", []), runtime.today,
            ).model_dump(mode="json")
            decision["dataset"] = apply_overrides(
                DatasetSpec.model_validate(decision["dataset"]), runtime.overrides).model_dump(mode="json")
            if decision["action"] == "accept":
                if not state["quality"]["usable"]:
                    decision.update(action="stop", reason="Planner acceptance rejected by deterministic data quality gate")
                elif not same_request(decision["dataset"], state["dataset"]):
                    decision.update(action="retry", reason="Planner changed dataset; new request must be downloaded first")
                else:
                    decision["dataset"] = state["dataset"]
        except Exception as error:
            warnings.append(f"Data review failed: {error}")
            decision = {"action": "stop", "reason": str(error), "dataset": state["dataset"], "deviations": []}
        if decision["action"] == "retry" and state["attempt"] >= runtime.max_data_attempts:
            decision.update(action="stop", reason="Data-attempt limit reached. " + decision["reason"])
        if decision["action"] == "retry" and same_request(decision["dataset"], state["dataset"]):
            decision.update(action="stop", reason="Planner proposed an identical failed request; no blind repetition")
        save_json(runtime.run_dir / f"data_decision_{state['attempt']}.json", decision)
        runtime.event("planner", f"Data decision: {decision['action']} — {decision['reason']}")
        update = {"decision": decision, "decisions": [decision], "warnings": warnings}
        if decision["action"] == "retry":
            update.update(dataset=decision["dataset"], attempt=state["attempt"] + 1)
        return update

    def prepare_experiments(state):
        if state["decision"]["action"] != "accept":
            return {"experiments": []}
        runtime.event("planner", "Freezing paper tests and proposing exploratory robustness tests before results")
        plan = ResearchPlan.model_validate(state["plan"])
        acquired = dict(state["acquisition"])
        acquired["evaluation_window"] = [state["quality"]["common_start"], state["quality"]["common_end"]]
        experiments, warnings = [], []
        for original in plan.experiments:
            data = original.model_dump(mode="json")
            deviations = data["deviations"] + [
                "Dataset provenance is unverified for an exact paper replication.",
                state["quality"]["sample_rule"],
            ] + state["decision"]["deviations"]
            # Preserve methodology. When the original period is unavailable, explicitly test the new sample.
            first, last = acquired["evaluation_window"]
            if ((data["end"] and data["end"] <= first) or (data["start"] and data["start"] > last)):
                deviations.append(f"Original period {data['start']} to {data['end']} unavailable; testing the acquired period")
                data.update(start=None, end=None)
            chosen_start, chosen_end = state["decision"].get("evaluation_start"), state["decision"].get("evaluation_end")
            if chosen_start or chosen_end:
                deviations.append(f"Planner selected evaluation period {chosen_start or 'available start'} to "
                                  f"{chosen_end or 'available end'} (exclusive end), separately from formation warmup")
                data.update(start=chosen_start, end=chosen_end)
            data.update(deviations=deviations)
            experiments.append(ExperimentSpec.model_validate(data))
        if runtime.max_extrapolations:
            try:
                suite = runtime.agents.extrapolate(plan, acquired, state.get("decisions", []),
                                                   runtime.max_extrapolations)
                ids = {e.id for e in experiments}
                for experiment in suite.experiments[:runtime.max_extrapolations]:
                    if experiment.kind != "extrapolation" or experiment.id in ids or experiment.published:
                        raise ValueError("Invalid extrapolation kind, duplicate ID or published-result attribution")
                    ids.add(experiment.id)
                experiments += suite.experiments[:runtime.max_extrapolations]
            except Exception as error:
                warnings.append(f"Extrapolation planner failed; using predefined cost/skip robustness tests: {error}")
                primary = next((e for e in experiments if e.method != "unsupported"), None)
                if primary:
                    for label, change in [("cost", {"fees_bps": max(10, primary.fees_bps)}),
                                          ("skip", {"skip_months": min(12, primary.skip_months + 1)})][
                                              :runtime.max_extrapolations]:
                        identifier = f"extra_{label}_{uuid4().hex[:6]}"
                        experiments.append(primary.model_copy(update={
                            **change, "id": identifier, "kind": "extrapolation", "published": [],
                            "name": f"Predefined {label} robustness", "methodology_matches_paper": False,
                            "hypothesis": f"Test sensitivity to {label} before observing results.",
                        }))
        payload = [e.model_dump(mode="json") for e in experiments]
        save_json(runtime.run_dir / "experiments.json", payload)
        save_json(runtime.run_dir / "selected_dataset.json", acquired)
        return {"experiments": payload, "acquisition": acquired, "warnings": warnings}

    def dispatch(state):
        if not state.get("experiments"):
            return "compile_report"
        return [Send("experiment", {"experiment": spec, "acquisition": state["acquisition"]})
                for spec in state["experiments"]]

    def experiment(state):
        spec = ExperimentSpec.model_validate(state["experiment"])
        runtime.event("experiment", f"Starting {spec.id}: {spec.name}")
        result = run_experiment(spec, state["acquisition"], runtime.run_dir / "experiments" / spec.id)
        save_json(runtime.run_dir / f"result_{spec.id}.json", result)
        runtime.event("experiment", f"{spec.id}: {result['status']}")
        return {"results": [result]}

    def compile_report(state):
        runtime.event("report", "Reviewing all results and compiling the final report")
        state = dict(state)
        state["results"] = sorted(state.get("results", []), key=lambda r: (r["spec"]["kind"] != "paper", r["id"]))
        save_json(runtime.run_dir / "results.json", state["results"])
        try:
            interpretation = runtime.agents.interpret({
                key: state.get(key) for key in ["plan", "acquisitions", "decisions", "results", "web_history", "warnings"]
            })
        except Exception as error:
            interpretation = f"Planner narrative unavailable: {error}. Computed results and limitations are recorded above."
        report = runtime.run_dir / "report.md"
        report.write_text(render_report(state, interpretation))
        save_json(runtime.run_dir / "state.json", state)
        runtime.event("report", f"Saved {report}")
        return {"report_path": str(report)}

    graph = StateGraph(ResearchState)
    for name, fn in [("planner", planner), ("collect_prices", collect_prices),
                     ("research_sources", research_sources), ("review_data", review_data),
                     ("prepare_experiments", prepare_experiments), ("experiment", experiment),
                     ("compile_report", compile_report)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "collect_prices")
    graph.add_edge("planner", "research_sources")
    graph.add_edge(["collect_prices", "research_sources"], "review_data")
    graph.add_conditional_edges("review_data", lambda s: s["decision"]["action"],
                               {"retry": "retry_data", "accept": "prepare_experiments", "stop": "prepare_experiments"})
    graph.add_node("retry_data", lambda state: {})
    graph.add_edge("retry_data", "collect_prices")
    graph.add_edge("retry_data", "research_sources")
    graph.add_conditional_edges("prepare_experiments", dispatch)
    graph.add_edge("experiment", "compile_report")
    graph.add_edge("compile_report", END)
    return graph.compile()


def apply_overrides(dataset, overrides):
    data = dataset.model_dump(mode="json")
    for field in ("symbols", "start", "end"):
        if overrides.get(field):
            data[field] = overrides[field]
    return DatasetSpec.model_validate(data)


def same_request(first, second):
    fields = ("source", "start", "end", "csv_url")
    return (all(first.get(field) == second.get(field) for field in fields)
            and set(first.get("symbols", [])) == set(second.get("symbols", [])))
