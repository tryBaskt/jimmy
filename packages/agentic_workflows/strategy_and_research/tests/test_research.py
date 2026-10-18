"""Offline tests for the bounded plan and research workflow."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from packages.agentic_workflows.strategy_and_research.plan import BedrockPlanner, RankingFactor, ResearchPlan
from packages.agentic_workflows.strategy_and_research.research import _factor_values, _targets, run_research
from packages.clients.vectorbt_client import VectorBTClient


def test_plan_rejects_unbounded_model_output():
    with pytest.raises(ValueError):
        ResearchPlan.from_model({
            "supported": True, "unsupported_reason": "", "universe": "SP500", "top_n": 10,
            "factors": [{"metric": "trailing_return", "direction": "highest", "weight": 100}],
            "fees_bps": 5, "slippage_bps": 5, "weighting": "equal", "code": "import os",
        }, 3)
    with pytest.raises(ValueError):
        ResearchPlan.from_model({
            "supported": True, "unsupported_reason": "", "universe": "SP500", "top_n": True,
            "factors": [{"metric": "trailing_return", "direction": "highest", "weight": 100}],
            "fees_bps": 5, "slippage_bps": 5, "weighting": "equal",
        }, 3)
    with pytest.raises(ValueError):
        ResearchPlan.from_model({
            "supported": True, "unsupported_reason": "", "universe": "SP500", "top_n": 10,
            "factors": [{"metric": "__import__('os')", "direction": "highest", "weight": 100}],
            "fees_bps": 5, "slippage_bps": 5, "weighting": "equal",
        }, 3)


def test_bedrock_tool_result_is_validated():
    response = {"output": {"message": {"content": [{"toolUse": {
        "name": "submit_research_plan",
        "input": {"supported": True, "unsupported_reason": "", "universe": "SP500", "top_n": 2,
                  "factors": [{"metric": "trailing_return", "direction": "lowest", "weight": 100}],
                  "fees_bps": 5, "slippage_bps": 5, "weighting": "equal"},
    }}]}}}
    client = SimpleNamespace(converse=lambda **kwargs: response)
    session = SimpleNamespace(client=lambda *args, **kwargs: client)
    plan = BedrockPlanner(session=session, model_id="test-model").plan("Momentum", 3)
    assert plan.lookback_months == 3
    assert plan.factors == (RankingFactor("trailing_return", "lowest", 100),)


def test_market_weight_request_never_reaches_backtest_or_model():
    def unexpected_call(**kwargs):
        pytest.fail("The model must not run for an unsupported weighting request")

    session = SimpleNamespace(client=lambda *args, **kwargs: SimpleNamespace(converse=unexpected_call))
    planner = BedrockPlanner(session=session, model_id="test-model")
    with pytest.raises(ValueError, match="historical market caps"):
        planner.plan("Select 10 biggest decliners and hold them at market weights", 3)
    with pytest.raises(ValueError, match="historical market caps"):
        ResearchPlan.from_model({
            "supported": True, "unsupported_reason": "", "universe": "SP500", "top_n": 10,
            "factors": [{"metric": "trailing_return", "direction": "lowest", "weight": 100}],
            "fees_bps": 5, "slippage_bps": 5, "weighting": "market_cap",
        }, 3)
    index = pd.bdate_range("2024-01-01", periods=3, tz="UTC")
    prices = pd.DataFrame({"A": [100.0, 101.0, 102.0]}, index=index)
    with pytest.raises(ValueError, match="historical market caps"):
        _targets(prices, prices["A"], ResearchPlan(
            "SP500", 1, 3, (RankingFactor("trailing_return", "lowest", 100),),
            5, 5, "market_cap",
        ))


def test_monthly_signal_uses_previous_close():
    index = pd.bdate_range("2024-01-01", "2024-04-15", tz="UTC")
    prices = pd.DataFrame({"A": 100.0, "B": 100.0}, index=index)
    # B jumps on the execution day, but A has the stronger known momentum.
    march_first = index[(index.month == 3)][0]
    prices.loc[index[index.month == 2], "A"] = 120.0
    prices.loc[march_first:, "A"] = 120.0
    prices.loc[march_first:, "B"] = 1000.0
    plan = ResearchPlan("SP500", 1, 1, (RankingFactor("trailing_return", "highest", 100),), 5, 5)
    targets, selections = _targets(prices, prices["A"], plan)
    march = next(x for x in selections if x["rebalance_date"] == march_first.date().isoformat())
    assert march["symbols"] == ["A"]
    assert march["lookback_start"] == "2024-01-29"
    assert march["signal_date"] == "2024-02-29"
    assert targets.loc[march_first, "A"] == 1.0


def test_buy_biggest_decliners_selects_lowest_prior_return():
    index = pd.bdate_range("2023-12-01", "2024-05-01", tz="UTC")
    prices = pd.DataFrame({"FALL": 100.0, "RISE": 100.0}, index=index)
    prices.loc[index[index.month == 3], "FALL"] = 50.0
    prices.loc[index[index.month == 3], "RISE"] = 150.0
    prices.loc[index[index.month >= 4], "FALL"] = 50.0
    prices.loc[index[index.month >= 4], "RISE"] = 150.0
    plan = ResearchPlan("SP500", 1, 3, (RankingFactor("trailing_return", "lowest", 100),), 5, 5)
    targets, selections = _targets(prices, prices["RISE"], plan)
    april = next(x for x in selections if x["rebalance_date"] == "2024-04-01")
    assert april["symbols"] == ["FALL"]
    assert targets.loc[pd.Timestamp("2024-04-01", tz="UTC"), "FALL"] == 1.0


def test_other_price_factors_are_computed_from_prior_window():
    index = pd.bdate_range("2024-01-01", periods=60, tz="UTC")
    window = pd.DataFrame({
        "A": np.linspace(100, 120, 60),
        "B": np.array([100 + ((i + 1) % 2) * 10 for i in range(60)]),
    }, index=index)
    benchmark = pd.Series(np.linspace(100, 110, 60), index=index)
    assert _factor_values("realized_volatility", window, benchmark)["B"] > _factor_values("realized_volatility", window, benchmark)["A"]
    assert _factor_values("drawdown_from_peak", window, benchmark)["B"] < _factor_values("drawdown_from_peak", window, benchmark)["A"]
    assert _factor_values("moving_average_gap", window, benchmark)["A"] > 0
    assert np.isfinite(_factor_values("market_beta", window, benchmark)["A"])


def test_research_runs_real_vectorbt_each_month():
    index = pd.bdate_range("2022-01-03", "2025-06-30", tz="UTC")
    n = len(index)
    prices = pd.DataFrame({
        "A": 100 * np.exp(np.arange(n) * 0.0007),
        "B": 100 * np.exp(np.arange(n) * 0.0004),
        "C": 100 * np.exp(np.arange(n) * 0.0002),
        "SPY": 100 * np.exp(np.arange(n) * 0.0003),
    }, index=index)

    class Membership:
        def list_universe_symbols(self, universe):
            assert universe == "SP500"
            return ["A", "B", "C", "MISSING"]

    class Prices:
        def get_stock_prices_over_time(self, symbols, start, end, timeframe):
            assert timeframe == "1d"
            frame = prices.reindex(columns=symbols)
            return frame.loc[(frame.index >= start) & (frame.index < end)]

    report = run_research(
        plan=ResearchPlan("SP500", 2, 3, (RankingFactor("trailing_return", "highest", 100),), 5, 5),
        membership_client=Membership(), price_client=Prices(),
        simulator=VectorBTClient(),
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end=datetime(2025, 7, 1, tzinfo=timezone.utc),
    )
    assert report["eligible_symbol_count"] == 3
    assert report["excluded_symbols"] == ["MISSING"]
    assert report["plan"]["lookback_months"] == 3
    assert report["start"] == "2023-01-01"
    assert len(report["selections"]) >= 24
    assert report["spy"]["total_return"] > 0
    assert len(report["performance_curve"]) > 2
    assert report["performance_curve"][-1]["spy"] == pytest.approx(
        1 + report["spy"]["total_return"]
    )
