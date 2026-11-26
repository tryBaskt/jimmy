import json
from pathlib import Path
from threading import Barrier

import httpx
import numpy as np
import pandas as pd
import pytest

from harness import workflow as main
from harness.tools import market_data
from harness.workflow import check_citations
from harness.tools.backtesting import build_weights, evaluate, run_experiment
from harness.models import DatasetSpec, ExperimentSpec, ResearchPlan
from harness.tools.market_data import monthly_returns, quality_check, validate_returns


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
                        original_data_requirements="Historical stocks", experiments=[experiment()],
                        extrapolations=[], limitations=["Modified universe"])


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


def test_exact_comparison_never_inferred_from_same_metric(tmp_path):
    published = {"metric": "mean_monthly_return", "value": .03, "tolerance": .1,
                 "citation": {"page": 1, "quote": "Winners outperform"}}
    result = run_experiment(experiment(published=[published]), acquired(tmp_path), tmp_path / "experiment")
    assert result["comparisons"][0]["outcome"] == "not_comparable"


def call_tool(name, **arguments):
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments),
            "status": "completed"}


def assess(text="Fixture assessment", reason=None):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "annotations": [],
                         "text": json.dumps({"assessment": text, "reason_no_experiments": reason})}]}


def investigation(tmp_path, monkeypatch, replies, *, workers=4):
    calls = []

    def handle(request):
        assert request.url.path == "/v1/responses"
        index = len(calls)
        calls.append(json.loads(request.content))
        output = replies[index] | {"id": f"item_{index}"}
        if output["type"] == "function_call":
            output["call_id"] = f"call_{index}"
        return httpx.Response(200, json={"id": f"resp_{index}", "object": "response", "created_at": 0,
                                        "model": "gpt-6.1-sol", "status": "completed", "output": [output]})

    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(main, "read_paper", lambda path: ["Winners outperform losers"])
    client = httpx.Client(transport=httpx.MockTransport(handle))
    model = main.chat_model("gpt-6.1-sol", max_tokens=12000, http_client=client)
    return main.Runtime(model, None, tmp_path, "2026-10-29", {}, workers=workers), calls


def fetch_batch(*datasets):
    return call_tool("collect_datasets", requests=[spec.model_dump(mode="json") for spec in datasets],
                     rationale="Compare coverage with the paper requirements")


def freeze(candidate_id, *ids, adjustments=()):
    return call_tool("freeze_experiments", candidate_id=candidate_id, experiment_ids=list(ids),
                     adjustments=list(adjustments), rationale="Closest usable data")


@pytest.mark.parametrize("assessment_valid", [True, False])
def test_pi_delegates_parallel_acquisition_and_backtests(monkeypatch, tmp_path, assessment_valid):
    from artifacts import RunStore

    store = RunStore(tmp_path)
    run_dir = store.runs / "fixture"
    run_dir.mkdir()
    original = plan()
    original.extrapolations = [experiment(id="extra_cost", kind="extrapolation", fees_bps=10)]
    assessment = assess()
    if not assessment_valid:
        assessment["content"][0]["text"] = "{}"
    config, calls = investigation(run_dir, monkeypatch, [
        call_tool("save_plan", proposal=original.model_dump(mode="json")),
        fetch_batch(dataset(), dataset(start="2000-02-01")),
        freeze(1, "momentum", "extra_cost"),
        call_tool("run_backtests"),
        assessment,
    ], workers=2)
    acquisition = acquired(tmp_path)
    collectors = Barrier(2)
    backtests = Barrier(2)

    def collect(*_):
        collectors.wait(timeout=5)
        return acquisition

    def execute(spec, *_):
        backtests.wait(timeout=5)
        return {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "completed",
                "metrics": {"months": 20, "mean_monthly_return": 0.02}}

    monkeypatch.setattr(main, "collect_candidate", collect)
    monkeypatch.setattr(main, "run_assigned_experiment", lambda _, spec, *args: execute(spec, *args))
    report = main.run_research(config, Path("paper.pdf")).read_text()
    snapshot = store.snapshot("fixture")
    assert [item["candidate_id"] for item in snapshot["acquisitions"]] == [1, 2]
    assert snapshot["completed_experiments"] == 2
    expected = "Fixture assessment" if assessment_valid else "The PI assessment could not be generated"
    assert expected in report
    assert "2.000%" in report
    assert len(calls) == 5
    assert all(call["store"] is False and call["parallel_tool_calls"] is False for call in calls)
    assert any(item["type"] == "function_call_output" and "mean_monthly_return" in item["output"]
               for item in calls[-1]["input"])
    assert calls[-1]["text"]["format"]["strict"] is True


def test_pi_enforces_four_rounds_and_can_revise_using_an_earlier_candidate(monkeypatch, tmp_path):
    original = plan()
    original.experiments[0].formation_months = 60
    edit = dict(id="momentum", start="2001-01-01", end="2003-01-01", formation_months=1,
                holding_months=1, skip_months=0, quantiles=2, reason="Shorter lookback for available history")
    config, calls = investigation(tmp_path, monkeypatch, [
        call_tool("save_plan", proposal=original.model_dump(mode="json")),
        fetch_batch(dataset()),
        fetch_batch(dataset(name="Duplicate request")),
        *[fetch_batch(dataset(start=f"2000-0{month}-01")) for month in range(2, 6)],
        freeze(1, "momentum", adjustments=[edit]),
        call_tool("run_backtests"),
        assess(),
    ])
    config.max_data_attempts = 5
    downloads = []
    acquisition = acquired(tmp_path)

    def collect(_model, spec, *_args):
        downloads.append(spec.start.month)
        if spec.start.month == 1:
            return acquisition
        return {"source": "fixture", "verified_available": False, "error": "Unavailable"}

    def execute(_, spec, selected, _folder):
        assert selected["candidate_id"] == 1
        assert spec.formation_months == 1
        assert str(spec.start) == "2001-01-01"
        return {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "completed"}

    monkeypatch.setattr(main, "collect_candidate", collect)
    monkeypatch.setattr(main, "run_assigned_experiment", execute)
    report = main.run_research(config, Path("paper.pdf")).read_text()
    assert downloads == [1, 2, 3, 4]
    assert "Shorter lookback" in report
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["plan"]["experiments"][0]["formation_months"] == 60
    assert state["results"][0]["spec"]["formation_months"] == 1
    assert "Data acquisition round limit reached" in json.dumps(calls[-1]["input"])


def test_pi_can_report_failed_acquisition_without_running_backtests(monkeypatch, tmp_path):
    config, calls = investigation(tmp_path, monkeypatch, [
        call_tool("save_plan", proposal=plan().model_dump(mode="json")),
        fetch_batch(dataset()),
        freeze(1, "momentum"),
        assess("Coverage is insufficient.", "No dataset could be verified."),
    ])
    monkeypatch.setattr(main, "collect_candidate", lambda *_: {
        "source": "fixture", "verified_available": False, "error": "No data"})
    report = main.run_research(config, Path("paper.pdf")).read_text()
    assert "No dataset could be verified." in report
    assert "Original claim not evaluated" in report
    assert "Select a candidate that was successfully collected" in json.dumps(calls[-1]["input"])
    assert not list(tmp_path.glob("result_*.json"))
