from pathlib import Path
from threading import Barrier

import numpy as np
import pandas as pd
import pytest

import workflow as main
import market_data
from tools import yahoo_finance
from workflow import check_citations
from backtesting import build_weights, evaluate, run_experiment
from models import DataDecision, DatasetSpec, ExperimentRevision, ExperimentSpec, InvestigationPlan, ResearchPlan
from market_data import monthly_returns, quality_check, validate_returns


def experiment(**updates):
    spec = dict(id="momentum", name="Momentum", kind="paper", method="cross_sectional_momentum",
                hypothesis="Winners outperform losers", formation_months=2, holding_months=1,
                skip_months=0, quantiles=2, fees_bps=0, slippage_bps=0, start=None, end=None,
                methodology_matches_paper=False, deviations=[], published=[])
    return ExperimentSpec.model_validate(spec | updates)


def dataset(**updates):
    return DatasetSpec.model_validate(dict(name="Test", source="yfinance", symbols=["A", "B"],
        start="2000-01-01", end="2005-01-01", csv_url=None, rationale="Test", known_differences=[]) | updates)


def plan():
    return ResearchPlan(title="Momentum paper", summary="Summary", main_claim="Winners outperform",
                        claim_citation={"page": 1, "quote": "Winners outperform"}, methodology="Rank past returns",
                        original_data_requirements="Historical stocks", dataset=dataset(),
                        experiments=[experiment()], limitations=["Modified universe"])


def matrix():
    dates = pd.date_range("2000-01-31", periods=48, freq="ME")
    return pd.DataFrame({"A": 0.02 + np.arange(48) * 0.0001, "B": -0.01}, index=dates)


def acquired(tmp_path):
    file = tmp_path / "data.csv"
    matrix().to_csv(file)
    return {"requested": dataset().model_dump(mode="json"), "source": "test_fixture",
            "verified_available": True, "returns_path": str(file), "coverage": {}, "limitations": []}


def test_no_future_signal_leakage():
    first = matrix()
    second = first.copy()
    second.iloc[20:] *= -4
    pd.testing.assert_frame_equal(build_weights(first, experiment()).iloc[:21],
                                  build_weights(second, experiment()).iloc[:21])


def test_skip_and_overlap_weights():
    frame = pd.DataFrame({"A": [.1, -.2, .1, -.2, .1], "B": [0., 0., 0., 0., 0.]},
                         index=pd.date_range("2020-01-31", periods=5, freq="ME"))
    weights = build_weights(frame, experiment(formation_months=1, holding_months=2, skip_months=1))
    assert weights.iloc[:3].isna().all().all()
    assert weights.iloc[3].tolist() == pytest.approx([0, 0])


def test_known_monthly_spread():
    frame = matrix()
    spec = experiment()
    series, _ = evaluate(frame, build_weights(frame, spec), spec)
    assert series.gross_return.iloc[0] == pytest.approx(.0302)
    assert len(series) == 46


def test_missing_held_security_fails():
    frame = matrix()
    weights = build_weights(frame, experiment())
    frame.iloc[10, 0] = np.nan
    with pytest.raises(ValueError, match="Held-security"):
        evaluate(frame, weights, experiment())


def test_costs_reduce_returns_and_include_initial_trade():
    frame = matrix()
    spec = experiment(fees_bps=10)
    series, _ = evaluate(frame, build_weights(frame, spec), spec)
    assert series.turnover.iloc[0] == pytest.approx(2)
    assert series.estimated_net_return.iloc[0] == pytest.approx(series.gross_return.iloc[0] - .002)
    assert (series.estimated_net_return <= series.gross_return).all()


def test_buy_hold_drifts_without_rebalance():
    frame = matrix()
    spec = experiment(method="buy_and_hold")
    series, weights = evaluate(frame, build_weights(frame, spec), spec)
    expected = ((1 + frame).cumprod().iloc[-1].mean()) - 1
    assert (1 + series.gross_return).prod() - 1 == pytest.approx(expected)
    assert weights.iloc[1, 0] > .5
    assert series.turnover.iloc[1:].max() == pytest.approx(0, abs=1e-12)


def test_time_series_signs():
    weights = build_weights(matrix(), experiment(method="time_series_momentum"))
    assert weights.iloc[2].tolist() == pytest.approx([.5, -.5])


def test_monthly_data_preserves_gaps_and_drops_partial_month():
    dates = pd.bdate_range("2020-01-01", "2020-04-15")
    prices = pd.DataFrame({"A": np.arange(len(dates)) + 100., "B": 100.}, index=dates)
    prices.loc["2020-02-14", "A"] = np.nan
    returns, _ = monthly_returns(prices, "2020-04-16")
    assert returns.index.max() == pd.Timestamp("2020-03-31")
    assert np.isnan(returns.loc["2020-02-29", "A"])
    assert np.isnan(returns.loc["2020-03-31", "A"])


@pytest.mark.parametrize("columns", ["single", "field_first", "ticker_first"])
def test_yahoo_close_shapes_and_adjustment(monkeypatch, columns):
    raw = pd.DataFrame({"Close": [100., 101.]}, index=pd.date_range("2020-01-01", periods=2))
    if columns == "field_first":
        raw.columns = pd.MultiIndex.from_tuples([("Close", "A")])
    elif columns == "ticker_first":
        raw.columns = pd.MultiIndex.from_tuples([("A", "Close")])
    calls = []
    def download(*args, **kwargs):
        calls.append(kwargs)
        return raw
    monkeypatch.setattr(yahoo_finance.yf, "download", download)
    result = market_data.YFinanceClient().get_stock_prices_over_time(["A"], "2020-01-01", "2020-02-01", "1D")
    assert list(result.columns) == ["A"]
    assert calls[0]["auto_adjust"] is True
    assert calls[0]["interval"] == "1d"


@pytest.mark.parametrize("body", [
    "date,security_id,return\n2020-01-01,A,.1\n2020-01-31,A,.2\n",
    "date,security_id,return\n2020-01-31,A,-2\n",
    "date,security_id,return\n2020-01-31,A,inf\n",
    "date,close\n2020-01-31,100\n",
])
def test_bad_csv_rejected(body):
    from io import StringIO
    with pytest.raises(ValueError):
        validate_returns(pd.read_csv(StringIO(body)))


def test_quality_cannot_accept_missing_ticker(tmp_path):
    acquisition = acquired(tmp_path)
    frame = matrix()
    frame["C"] = np.nan
    frame.to_csv(acquisition["returns_path"])
    assert not quality_check(acquisition, [experiment()])["usable"]


def test_quality_selects_common_contiguous_window(tmp_path):
    acquisition = acquired(tmp_path)
    frame = matrix()
    frame.iloc[:5, 0] = np.nan
    frame.to_csv(acquisition["returns_path"])
    quality = quality_check(acquisition, [experiment()])
    assert quality["usable"]
    assert quality["common_start"] == "2000-06-30"


def test_paper_citation_must_exist():
    check_citations(plan(), ["Winners outperform losers"])
    with pytest.raises(ValueError, match="Unverified paper quote"):
        check_citations(plan(), ["Entirely different text"])


def test_vectorbt_uses_previous_close_and_matches_gross(tmp_path):
    result = run_experiment(experiment(), acquired(tmp_path), tmp_path / "experiment")
    assert result["status"] == "completed", result
    assert "vectorbt_error" not in result, result
    assert result["vectorbt"]["final_account_return"] == pytest.approx(
        result["metrics"]["hypothetical_total_return"], rel=1e-7)
    assert result["metrics"]["hac_lags"] == 0


def test_vectorbt_buy_hold_with_fees(tmp_path):
    result = run_experiment(experiment(method="buy_and_hold", fees_bps=10), acquired(tmp_path), tmp_path / "experiment")
    assert result["status"] == "completed", result
    assert "vectorbt_error" not in result, result
    assert result["vectorbt"]["filled_orders"] == 2
    assert result["vectorbt"]["final_account_return"] < result["metrics"]["hypothetical_total_return"]


def test_unsupported_method_visible(tmp_path):
    result = run_experiment(experiment(method="unsupported"), acquired(tmp_path), tmp_path / "experiment")
    assert result["status"] == "failed"
    assert "not implemented" in result["error"]


def decision(action="accept", candidate=1, requests=(), reason="Fixture coverage passes", **kwargs):
    return DataDecision(action=action, selected_candidate=candidate, requests=list(requests),
                        reason=reason, deviations=["Test sample"],
                        **({"evaluation_start": None, "evaluation_end": None} | kwargs))


def failed_experiment(spec, *_):
    return {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "failed",
            "error": "Fixture demonstrates visible failures"}


class FakeInvestigator:
    def __init__(self, decisions=None, *, alternatives=(), collect=None, execute=failed_experiment, revision=None):
        self.decisions = decisions or []
        self.reviews = 0
        self.alternatives = list(alternatives)
        self.delegate_collection = collect
        self.delegate_experiment = execute
        self.revision = revision

    def plan(self, pages, today, overrides, maximum):
        return InvestigationPlan(**plan().model_dump(), alternative_datasets=self.alternatives,
                                 extrapolations=[experiment(id="extra_cost", kind="extrapolation", fees_bps=10)][:maximum])

    def review_data(self, plan, candidates, history, remaining, today):
        self.reviews += 1
        return self.decisions[self.reviews - 1] if self.decisions else decision()

    def revise_experiments(self, *args):
        return self.revision or ExperimentRevision(selected_candidate=None, experiments=[], rationale="Insufficient data")

    def interpret(self, evidence):
        return "Interpretation based on fixture results only."


def runtime(tmp_path, investigator, monkeypatch):
    monkeypatch.setattr(main, "plan_research", lambda model, folder, *args: investigator.plan(*args))
    monkeypatch.setattr(main, "review_collected_data", lambda model, folder, *args: investigator.review_data(*args))
    monkeypatch.setattr(main, "revise_experiments", lambda model, folder, *args: investigator.revise_experiments(*args))
    monkeypatch.setattr(main, "write_assessment", lambda model, folder, evidence: investigator.interpret(evidence))
    monkeypatch.setattr(main, "collect_candidate", lambda model, *args: investigator.delegate_collection(*args))
    monkeypatch.setattr(main, "run_assigned_experiment", lambda model, *args: investigator.delegate_experiment(*args))
    return main.Runtime(None, None, tmp_path, "2026-10-29", {}, 4, 1)


def test_graph_parallel_acquisition_and_experiment_fanout(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    collectors = Barrier(2)
    experiments = Barrier(2)

    def fetch(*args):
        collectors.wait(timeout=10)
        return dict(acquisition)

    def execute(spec, *_):
        experiments.wait(timeout=10)
        return failed_experiment(spec)

    investigator = FakeInvestigator(alternatives=[dataset(start="2000-02-01")], collect=fetch, execute=execute)
    result = main.create_workflow(runtime(tmp_path, investigator, monkeypatch)).invoke({"paper_path": "fixture.pdf"},
                                                                       {"max_concurrency": 4})
    assert len(result["acquisitions"]) == 2
    assert {c["candidate_id"] for c in result["acquisitions"]} == {1, 2}
    assert len(result["results"]) == 2
    assert all(r["error"] == "Fixture demonstrates visible failures" for r in result["results"])
    assert investigator.reviews == 1
    assert "original claim not evaluated" in Path(result["report_path"]).read_text().lower()


def test_graph_data_retry_then_accept(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    later = dataset(name="Substitute", start="2000-02-01")
    decisions = [decision("retry", requests=[later], reason="Use substitute"), decision(candidate=5)]
    investigator = FakeInvestigator(decisions, collect=lambda *_: dict(acquisition))
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    result = main.create_workflow(runtime(tmp_path, investigator, monkeypatch)).invoke({"paper_path": "fixture.pdf"})
    assert len(result["acquisitions"]) == 2
    assert result["attempt"] == 2
    assert result["acquisition"]["candidate_id"] == 5
    assert len(result["results"]) == 2


def test_graph_rejects_acceptance_of_failed_data(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    investigator = FakeInvestigator(collect=lambda *_: {
        "source": "fixture", "verified_available": False, "error": "No data"})
    result = main.create_workflow(runtime(tmp_path, investigator, monkeypatch)).invoke({"paper_path": "fixture.pdf"})
    assert result["decision"]["action"] == "stop"
    assert not result.get("results")
    assert Path(result["report_path"]).is_file()


def test_exact_comparison_never_inferred_from_same_metric(tmp_path):
    published = {"metric": "mean_monthly_return", "value": .03, "tolerance": .1,
                 "citation": {"page": 1, "quote": "Winners outperform"}}
    result = run_experiment(experiment(published=[published]), acquired(tmp_path), tmp_path / "experiment")
    assert result["comparisons"][0]["outcome"] == "not_comparable"


def test_selected_evaluation_period_excludes_warmup(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    selected = decision(reason="Explicit period", evaluation_start="2001-01-01", evaluation_end="2003-01-01")
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    captured = []

    def execute(spec, *_):
        captured.append(spec)
        return failed_experiment(spec)

    investigator = FakeInvestigator([selected], collect=lambda *_: dict(acquisition), execute=execute)
    result = main.create_workflow(runtime(tmp_path, investigator, monkeypatch)).invoke({"paper_path": "fixture.pdf"})
    paper = next(spec for spec in captured if spec.kind == "paper")
    assert str(paper.start) == "2001-01-01"
    assert str(paper.end) == "2003-01-01"
    assert "excluding warmup" in Path(result["report_path"]).read_text()


def test_four_round_limit_and_revision_use_earlier_collected_data(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    retries = [decision("retry", requests=[dataset(start=f"2000-0{i}-01")]) for i in range(2, 6)]
    revision = ExperimentRevision(selected_candidate=1, rationale="Use original coverage with a shorter lookback",
        experiments=[dict(id="momentum", start=None, end=None, formation_months=1, holding_months=1,
                          skip_months=0, quantiles=2, reason="Shorter lookback for available history")])
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    original = plan()
    original.experiments[0].formation_months = 60

    def fetch(spec, *_):
        if spec.start.month == 1:
            return dict(acquisition)
        return {"source": "fixture", "verified_available": False, "error": "Unavailable"}

    investigator = FakeInvestigator(retries, collect=fetch, revision=revision)
    investigator.plan = lambda *args: InvestigationPlan(**original.model_dump(), alternative_datasets=[], extrapolations=[])
    config = runtime(tmp_path, investigator, monkeypatch)
    config.max_data_attempts = 5
    result = main.create_workflow(config).invoke({"paper_path": "fixture.pdf"})
    assert result["decision"]["action"] == "stop"
    assert "limit reached" in result["decision"]["reason"]
    assert investigator.reviews == 4
    assert len(result["acquisitions"]) == 4
    assert result["acquisition"]["candidate_id"] == 1
    assert result["plan"]["experiments"][0]["formation_months"] == 60
    assert result["results"][0]["spec"]["formation_months"] == 1
    assert "Shorter lookback" in Path(result["report_path"]).read_text()


def test_metadata_changes_do_not_trigger_another_download():
    first = dataset().model_dump(mode="json")
    second = dict(first, name="New description", rationale="Different rationale")
    assert main.same_request(first, second)
