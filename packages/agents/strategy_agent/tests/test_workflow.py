from datetime import datetime, timezone
import logging

import pandas as pd
import pytest

from packages.agents.strategy_agent.tools.backtest import replay_portfolios
from packages.agents.strategy_agent.plan import StrategyPlan
from packages.agents.strategy_agent.workflow import run_strategy


def test_rebalance_uses_prior_close_and_saved_decisions_replay(caplog):
    dates = pd.bdate_range("2025-01-01", "2025-07-01", tz="UTC")
    prices = pd.DataFrame({
        "AAA": [100 + i * 0.2 for i in range(len(dates))],
        "BBB": [100 - i * 0.1 for i in range(len(dates))],
        "SPY": [100 + i * 0.05 for i in range(len(dates))],
    }, index=dates)

    class Agent:
        model_id = "test"

        def plan(self, prompt, lookback):
            return StrategyPlan(prompt, "SP500", 1, lookback, "monthly",
                                "trailing_return", "highest", "equal", False, 5, 5)

        def decide(self, plan, signal_date, signals):
            assert signal_date < "2025-07-01"
            assert signals[0]["symbol"] == "AAA"
            return {"positions": [{"symbol": "AAA", "direction": "long",
                                    "weight": 1, "reason": "Highest prior return"}]}

    with caplog.at_level(logging.INFO, logger="packages.agents.strategy_agent.workflow"):
        report = run_strategy(
            prompt="Buy the strongest prior performer", lookback_months=1,
            agent=Agent(), universe_loader=lambda _: ["AAA", "BBB"],
            price_loader=lambda symbols, start, end: prices,
            start=datetime(2025, 3, 1, tzinfo=timezone.utc),
            end=datetime(2025, 7, 1, tzinfo=timezone.utc),
            include_replay_prices=True,
            job_id="test-job",
        )
    assert "Strategy job test-job stage=planning" in caplog.text
    assert "stage=prices_ready" in caplog.text
    assert "stage=rebalance_ready" in caplog.text
    assert "stage=backtest_ready" in caplog.text
    assert "stage=report_ready" in caplog.text
    assert "Buy the strongest prior performer" not in caplog.text
    assert report["decisions"][0]["signal_date"] < report["decisions"][0]["rebalance_date"]
    assert report["decisions"][0]["lookback_start"] < report["decisions"][0]["signal_date"]
    assert len(report["decisions"]) == 4
    assert report["run_record"]["run_id"]
    assert [event["kind"] for event in report["run_record"]["events"]].count("portfolio_decision") == 4
    replay = pd.DataFrame(report["replay_prices"]).set_index("date")
    replay.index = pd.to_datetime(replay.index, utc=True)
    again = replay_portfolios(
        replay[["AAA"]], replay["SPY"], report["decisions"],
        fees_bps=5, slippage_bps=5,
    )
    assert again == {key: report[key] for key in again}


def test_replay_rejects_a_missing_selected_price():
    dates = pd.bdate_range("2025-03-01", periods=3, tz="UTC")
    prices = pd.DataFrame({"AAA": [100, 101, 102]}, index=dates)
    decision = [{"rebalance_date": dates[0].date().isoformat(), "positions": [
        {"symbol": "MISSING", "direction": "long", "weight": 1.0}
    ]}]
    with pytest.raises(ValueError, match="No price"):
        replay_portfolios(prices, prices["AAA"], decision, fees_bps=0, slippage_bps=0)


def test_market_cap_weights_use_previous_close_and_replace_model_weights():
    dates = pd.bdate_range("2025-01-01", "2025-07-01", tz="UTC")
    prices = pd.DataFrame({
        "AAA": [100 + i * 0.2 for i in range(len(dates))],
        "BBB": [100 - i * 0.1 for i in range(len(dates))],
        "SPY": [100 + i * 0.05 for i in range(len(dates))],
    }, index=dates)
    caps = pd.DataFrame(
        [
            (day, symbol, 300.0 if symbol == "AAA" else 100.0)
            for day in dates for symbol in ("AAA", "BBB")
        ],
        columns=["timestamp", "symbol", "market_cap"],
    ).set_index(["timestamp", "symbol"])
    caps.loc[(pd.Timestamp("2025-03-03", tz="UTC"), "AAA"), "market_cap"] = 1.0
    caps.loc[(pd.Timestamp("2025-03-03", tz="UTC"), "BBB"), "market_cap"] = 1000.0

    class Agent:
        model_id = "test"

        def plan(self, prompt, lookback):
            return StrategyPlan(prompt, "SP500", 2, lookback, "monthly",
                                "trailing_return", "highest", "market_cap", False, 0, 0)

        def decide(self, plan, signal_date, signals):
            return {"positions": [
                {"symbol": "AAA", "direction": "long", "weight": 0.5, "reason": "Prior return"},
                {"symbol": "BBB", "direction": "long", "weight": 0.5, "reason": "Prior return"},
            ]}

    report = run_strategy(
        prompt="Buy two prior performers at market weights",
        lookback_months=1,
        agent=Agent(),
        universe_loader=lambda _: ["AAA", "BBB"],
        price_loader=lambda symbols, start, end: prices,
        market_cap_loader=lambda symbols, start, end: caps,
        start=datetime(2025, 3, 1, tzinfo=timezone.utc),
        end=datetime(2025, 7, 1, tzinfo=timezone.utc),
    )
    first = report["decisions"][0]
    assert first["signal_date"] == "2025-02-28"
    assert first["rebalance_date"] == "2025-03-03"
    assert [position["weight"] for position in first["positions"]] == [0.75, 0.25]
    assert report["market_cap_data_sha256"]
    assert any(event["kind"] == "market_cap_weights" for event in report["run_record"]["events"])


def test_missing_market_caps_get_equal_slots_from_remaining_weight():
    dates = pd.bdate_range("2025-01-01", "2025-07-01", tz="UTC")
    prices = pd.DataFrame({
        "AAA": [100 + i * 0.3 for i in range(len(dates))],
        "BBB": [100 + i * 0.2 for i in range(len(dates))],
        "CCC": [100 + i * 0.1 for i in range(len(dates))],
        "SPY": [100 + i * 0.05 for i in range(len(dates))],
    }, index=dates)
    caps = pd.DataFrame(
        [(day, symbol, {"AAA": 300.0, "BBB": 100.0, "CCC": float("nan")}[symbol])
         for day in dates for symbol in ("AAA", "BBB", "CCC")],
        columns=["timestamp", "symbol", "market_cap"],
    ).set_index(["timestamp", "symbol"])
    caps.loc[(pd.Timestamp("2025-03-31", tz="UTC"), slice(None)), "market_cap"] = float("nan")

    class Agent:
        model_id = "test"

        def plan(self, prompt, lookback):
            return StrategyPlan(prompt, "SP500", 3, lookback, "monthly",
                                "trailing_return", "highest", "market_cap", False, 0, 0)

        def decide(self, plan, signal_date, signals):
            return {"positions": [
                {"symbol": symbol, "direction": "long", "weight": 1 / 3, "reason": "Prior return"}
                for symbol in ("AAA", "BBB", "CCC")
            ]}

    report = run_strategy(
        prompt="Buy three stocks at market weights", lookback_months=1,
        agent=Agent(), universe_loader=lambda _: ["AAA", "BBB", "CCC"],
        price_loader=lambda symbols, start, end: prices,
        market_cap_loader=lambda symbols, start, end: caps,
        start=datetime(2025, 3, 1, tzinfo=timezone.utc),
        end=datetime(2025, 7, 1, tzinfo=timezone.utc),
    )
    first = report["decisions"][0]
    assert [position["weight"] for position in first["positions"]] == pytest.approx([0.5, 1 / 6, 1 / 3])
    assert [position["market_cap"] for position in first["positions"]] == [300.0, 100.0, None]
    second = report["decisions"][1]
    assert [position["weight"] for position in second["positions"]] == pytest.approx([1 / 3] * 3)
    events = [event for event in report["run_record"]["events"] if event["kind"] == "market_cap_weights"]
    assert events[0]["details"]["fallback_symbols"] == ["CCC"]
    assert events[1]["details"]["fallback_symbols"] == ["AAA", "BBB", "CCC"]
    assert any("without a valid market cap" in limitation for limitation in report["limitations"])


def test_random_selection_replays_deterministically_without_price_ranking():
    dates = pd.bdate_range("2025-01-01", "2025-07-01", tz="UTC")
    symbols = [f"S{i:02d}" for i in range(25)]
    prices = pd.DataFrame({symbol: [100 + i * 0.1 for i in range(len(dates))]
                           for symbol in symbols + ["SPY"]}, index=dates)

    class Agent:
        model_id = "test"

        def plan(self, prompt, lookback):
            return StrategyPlan.from_model({
                "supported": True, "hypothesis": prompt, "universe": "SP500",
                "top_n": 15, "rebalance": "monthly", "selection_steps": [{"operation": "sample"}],
                "weighting": "equal", "allow_short": False, "fees_bps": 0, "slippage_bps": 0,
            }, lookback)

        def decide(self, *args):
            pytest.fail("Declarative selection should not call the model per rebalance")

    kwargs = dict(prompt="Choose 15 S&P 500 stocks randomly each month", lookback_months=2,
                  agent=Agent(), universe_loader=lambda _: symbols,
                  price_loader=lambda requested, start, end: prices,
                  start=datetime(2025, 3, 1, tzinfo=timezone.utc),
                  end=datetime(2025, 7, 1, tzinfo=timezone.utc))
    first = run_strategy(**kwargs)
    second = run_strategy(**kwargs)
    assert first["random_seed"] == second["random_seed"]
    assert first["decisions"] == second["decisions"]
    assert len(first["decisions"]) == 4
    assert all(len(set(decision["symbols"])) == 15 for decision in first["decisions"])
    assert all(decision["candidate_count"] == 15 for decision in first["decisions"])
    assert all(decision["lookback_start"] is None for decision in first["decisions"])
