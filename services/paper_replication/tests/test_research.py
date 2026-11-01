from pathlib import Path
from threading import Barrier

import numpy as np
import pandas as pd
import pytest

import workflow as main
import market_data
from agents import check_citations
from backtesting import build_weights, evaluate, run_experiment
from models import DataDecision, DatasetSpec, ExperimentSpec, ExperimentSuite, ResearchPlan
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
    monkeypatch.setattr(market_data.yf, "download", download)
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


class FakeAgents:
    def __init__(self, decisions=None):
        self.decisions = decisions or []
        self.reviews = 0

    def plan(self, *args):
        return plan()

    def search(self, *args):
        return {"notes": "Mocked discovery, no network used", "sources": {}, "status": "completed"}

    def review_data(self, *args):
        i = self.reviews
        self.reviews += 1
        return self.decisions[i] if self.decisions else DataDecision(
            action="accept", reason="Fixture coverage passes", dataset=dataset(), deviations=["Test sample"])

    def extrapolate(self, *args):
        return ExperimentSuite(experiments=[experiment(id="extra_cost", kind="extrapolation", fees_bps=10)],
                               rationale="Cost sensitivity")

    def interpret(self, evidence):
        assert len(evidence["results"]) >= 1
        return "Interpretation based on fixture results only."


def runtime(tmp_path, agents):
    return main.Runtime(agents, tmp_path, "2026-10-29", {}, 2, 1, web_search=True)


def test_graph_parallel_fanout_and_join(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    collectors = Barrier(2)
    experiments = Barrier(2)
    def fetch(*args):
        collectors.wait(timeout=10)
        return acquisition
    agents = FakeAgents()
    def search(*args):
        collectors.wait(timeout=10)
        return {"notes": "Fixture", "sources": {}}
    agents.search = search
    monkeypatch.setattr(main, "acquire", fetch)
    def execute(spec, acquisition, folder):
        experiments.wait(timeout=10)
        return {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "failed",
                "error": "Fixture demonstrates visible failures"}
    monkeypatch.setattr(main, "run_experiment", execute)
    result = main.create_workflow(runtime(tmp_path, agents)).invoke({"paper_path": "fixture.pdf"},
                                                                 {"max_concurrency": 4})
    assert len(result["results"]) == 2
    assert agents.reviews == 1
    assert Path(result["report_path"]).is_file()
    assert "original claim not evaluated" in Path(result["report_path"]).read_text().lower()


def test_graph_data_retry_then_accept(monkeypatch, tmp_path):
    acquisition = acquired(tmp_path)
    later = dataset(name="Substitute", start="2000-02-01")
    decisions = [DataDecision(action="retry", reason="Use substitute", dataset=later, deviations=["New dates"]),
                 DataDecision(action="accept", reason="Fetched", dataset=later, deviations=["New dates"])]
    agents = FakeAgents(decisions)
    def fetch(spec, *args):
        return acquisition | {"requested": spec.model_dump(mode="json")}
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    monkeypatch.setattr(main, "acquire", fetch)
    monkeypatch.setattr(main, "run_experiment", lambda spec, *_: {
        "id": spec.id, "spec": spec.model_dump(mode="json"), "status": "failed", "error": "Fixture"})
    result = main.create_workflow(runtime(tmp_path, agents)).invoke({"paper_path": "fixture.pdf"})
    assert len(result["acquisitions"]) == 2
    assert result["attempt"] == 2
    assert len(result["results"]) == 2


def test_graph_rejects_acceptance_of_failed_data(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    monkeypatch.setattr(main, "acquire", lambda *args: {
        "requested": dataset().model_dump(mode="json"), "source": "fixture",
        "verified_available": False, "error": "No data"})
    result = main.create_workflow(runtime(tmp_path, FakeAgents())).invoke({"paper_path": "fixture.pdf"})
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
    agents = FakeAgents([DataDecision(action="accept", reason="Explicit period", dataset=dataset(),
                                      deviations=[], evaluation_start="2001-01-01", evaluation_end="2003-01-01")])
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    monkeypatch.setattr(main, "acquire", lambda *args: acquisition)
    captured = []
    def execute(spec, *_):
        captured.append(spec)
        return {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "failed", "error": "Fixture"}
    monkeypatch.setattr(main, "run_experiment", execute)
    result = main.create_workflow(runtime(tmp_path, agents)).invoke({"paper_path": "fixture.pdf"})
    paper = next(spec for spec in captured if spec.kind == "paper")
    assert str(paper.start) == "2001-01-01"
    assert str(paper.end) == "2003-01-01"
    assert "excluding warmup" in Path(result["report_path"]).read_text()


def test_retry_limit_is_enforced(monkeypatch, tmp_path):
    later = dataset(start="2000-02-01")
    agents = FakeAgents([DataDecision(action="retry", reason="Change data", dataset=later, deviations=[])])
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    monkeypatch.setattr(main, "acquire", lambda *args: {
        "requested": dataset().model_dump(mode="json"), "source": "fixture",
        "verified_available": False, "error": "Unavailable"})
    config = runtime(tmp_path, agents)
    config.max_data_attempts = 1
    result = main.create_workflow(config).invoke({"paper_path": "fixture.pdf"})
    assert result["decision"]["action"] == "stop"
    assert "limit reached" in result["decision"]["reason"]
    assert agents.reviews == 1


def test_metadata_changes_do_not_trigger_another_download():
    first = dataset().model_dump(mode="json")
    second = dict(first, name="New description", rationale="Different rationale")
    assert main.same_request(first, second)
