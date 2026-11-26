"""Write an auditable report even when data acquisition or individual experiments fail."""

import json
from pathlib import Path


def save_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str, allow_nan=False))
    temporary.replace(path)


def render_report(state: dict, interpretation: str) -> str:
    plan = state["plan"]
    results = state.get("results", [])
    completed = [r for r in results if r["status"] == "completed"]
    verdict = ("Modified-data tests completed; original paper claim remains unverified."
               if any(r["spec"]["kind"] == "paper" for r in completed)
               else "Original claim not evaluated: no paper experiment completed.")
    lines = [f"# {plan['title']}", "", f"**Assessment: {verdict}**", "",
             "## Paper and frozen methodology", "", plan["summary"], "",
             f"Main claim: {plan['main_claim']}", "",
             f"Evidence: PDF page {plan['claim_citation']['page']}: “{plan['claim_citation']['quote']}”", "",
             plan["methodology"], "", "Original data requirements: " + plan["original_data_requirements"], "",
             "## Data acquisition and substitutions", ""]
    planned_details = ["## Original experiment plan and coverage", ""]
    executed_ids = {result["id"] for result in results}
    for experiment in plan["experiments"] + plan.get("extrapolations", []):
        status = "execution recorded below" if experiment["id"] in executed_ids else "not executed"
        planned_details += [f"- **{experiment['name']}** ({experiment['kind']}): {status}, "
                            f"method={experiment['method']}."]
        planned_details += [f"  - Methodology gap: {item}" for item in experiment["deviations"]]
        for expected in experiment["published"]:
            planned_details += [f"  - Published {expected['metric']}: {expected['value']} "
                                f"(PDF page {expected['citation']['page']}: “{expected['citation']['quote']}”)."]
    planned_details += [""]
    lines[-2:-2] = planned_details
    for attempt in state.get("acquisitions", []):
        request = attempt["requested"]
        lines += [f"### Round {attempt['round']}, candidate {attempt['candidate_id']}: {request['name']}", "",
                  f"Source: {attempt['source']}; requested {request['start']} to {request['end']} (exclusive).",
                  f"Availability verified by download: {attempt['verified_available']}.", "",
                  f"Symbols: {', '.join(request['symbols']) or 'CSV identifiers'}.", ""]
        if attempt.get("error"):
            lines += ["Acquisition failed: " + attempt["error"], ""]
        lines += [f"- {item}" for item in request["known_differences"] + attempt.get("limitations", [])]
        lines += ["", "Coverage:", "", "```json", json.dumps(attempt.get("coverage", {}), indent=2), "```", ""]
        if attempt.get("sha256"):
            lines += [f"Snapshot SHA-256: `{attempt['sha256']}`", ""]
        lines += [f"- Coverage issue: {issue}" for issue in attempt["quality"]["issues"]] + [""]
    for decision in state.get("decisions", []):
        lines += [f"Planner data decision: **{decision['action']}** — {decision['reason']}", ""]
        if decision.get("evaluation_start") or decision.get("evaluation_end"):
            lines += [f"Chosen evaluation period: {decision.get('evaluation_start')} to "
                      f"{decision.get('evaluation_end')} (exclusive end), excluding warmup.", ""]
        lines += [f"- {item}" for item in decision["deviations"]] + [""]
    if state.get("acquisition"):
        lines += [f"Dataset selected for backtests: candidate {state['acquisition']['candidate_id']}.", ""]
    if state.get("revision"):
        lines += ["### PI experiment revision", "", state["revision"]["rationale"], ""]
    for kind, title in [("paper", "Paper experiments (modified tests)"),
                        ("extrapolation", "Exploratory extrapolations")]:
        lines += [f"## {title}", "",
                  "| Experiment | Status | Sample | Months | Mean gross monthly | Estimated net monthly | HAC t | Sharpe |",
                  "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for result in results:
            spec = result["spec"]
            if spec["kind"] != kind:
                continue
            metrics, net = result.get("metrics", {}), result.get("estimated_net_metrics", {})
            def number(value, percent=False):
                return "—" if value is None else (f"{value:.3%}" if percent else f"{value:.3f}")
            sample = f"{result.get('start', '—')} / {result.get('end', '—')}"
            lines += [f"| {spec['name'].replace('|', '/')} | {result['status']} | {sample} | "
                      f"{metrics.get('months', '—')} | {number(metrics.get('mean_monthly_return'), True)} | "
                      f"{number(net.get('mean_monthly_return'), True)} | "
                      f"{number(metrics.get('hac_t_stat'))} | {number(metrics.get('annualized_sharpe'))} |"]
        lines += [""]
        for result in results:
            spec = result["spec"]
            if spec["kind"] != kind:
                continue
            lines += [f"### {spec['name']}", "", spec["hypothesis"], "",
                      f"Method: {spec['method']}; J={spec['formation_months']}, K={spec['holding_months']}, "
                      f"skip={spec['skip_months']}, quantiles={spec['quantiles']}; "
                      f"fees={spec['fees_bps']} bps, slippage={spec['slippage_bps']} bps per traded notional.", ""]
            lines += [f"- {item}" for item in spec["deviations"]] + [""]
            if result.get("error"):
                lines += ["Failed: " + result["error"], ""]
            for comparison in result.get("comparisons", []):
                lines += [f"Published comparison `{comparison['metric']}`: published={comparison['published']}, "
                          f"observed={comparison['observed']}, difference={comparison['difference']}, "
                          f"tolerance={comparison['tolerance']}. **{comparison['outcome']}**. "
                          f"PDF page {comparison['citation']['page']}: “{comparison['citation']['quote']}”.", ""]
            if result.get("vectorbt"):
                lines += ["VectorBT execution diagnostic:", "", "```json",
                          json.dumps(result["vectorbt"], indent=2), "```", ""]
            if result.get("vectorbt_error"):
                lines += ["VectorBT diagnostic failed (gross calculation retained): " + result["vectorbt_error"], ""]
    lines += ["## Principal Investigator assessment", "", interpretation, "", "## Conventions and limits", "",
              "- Modified data and simplified methodology cannot establish an exact replication.",
              "- Cross-sectional momentum is +1 long / -1 short: a spread per dollar long, gross exposure 2. "
              "Compounded spread wealth is hypothetical, not an investable return guarantee.",
              "- Cohorts are rebalanced within each month. Weekly delays, factor regressions and value weights "
              "are not implemented; methodology differences remain explicit.",
              "- HAC mean-return t statistics use K−1 lags. Sharpe uses 12 periods/year and zero risk-free rate.",
              "- Estimated net returns subtract turnover × (fees + slippage); this is a first-order cost estimate. "
              "VectorBT independently simulates account execution. No borrow fees or market impact.",
              "- Monthly-close execution assumes the signal can be filled at its formation close. "
              "No next-open execution delay is modeled; this may overstate implementable performance.",
              "- Extrapolations were planned before results, but use the same data and remain exploratory. "
              "No multiple-testing correction or untouched holdout is claimed.", ""]
    lines += [f"- {item}" for item in plan["limitations"]] + [""]
    for error in state.get("warnings", []):
        lines += [f"- Workflow warning: {error}"]
    return "\n".join(lines)
