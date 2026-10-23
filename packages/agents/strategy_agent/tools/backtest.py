"""Deterministically replay dated target portfolios with VectorBT."""

from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd
from vectorbt.portfolio.base import Portfolio


def _metrics(returns: pd.Series) -> dict:
    if returns.empty:
        raise ValueError("The backtest needs at least one trading day")
    growth = (1 + returns).cumprod()
    volatility = returns.std(ddof=1)
    return {
        "total_return": float(growth.iloc[-1] - 1),
        "annualized_return": float(growth.iloc[-1] ** (252 / len(returns)) - 1),
        "sharpe": float(returns.mean() / volatility * sqrt(252))
        if pd.notna(volatility) and volatility > 0 else None,
        "max_drawdown": float((growth / growth.cummax() - 1).min()),
    }


def replay_portfolios(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    decisions: list[dict],
    *,
    fees_bps: int,
    slippage_bps: int,
    initial_cash: float = 10_000,
) -> dict:
    """Run a fixed list of decisions; no model or external data calls occur here."""
    if prices.empty or not prices.index.equals(benchmark.index):
        raise ValueError("Price and benchmark dates must match")
    if not prices.index.is_unique or not prices.index.is_monotonic_increasing:
        raise ValueError("Price dates must be sorted and unique")
    numeric = prices.to_numpy(dtype="float64")
    if not np.isfinite(numeric).all() or (numeric <= 0).any():
        raise ValueError("Backtest prices must be complete and positive")
    if not np.isfinite(benchmark.to_numpy(dtype="float64")).all():
        raise ValueError("Benchmark prices must be complete")
    targets = pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    seen = set()
    for decision in decisions:
        date = pd.Timestamp(decision["rebalance_date"])
        matching = prices.index[prices.index.date == date.date()]
        if len(matching) != 1 or date.date() in seen:
            raise ValueError(f"Invalid or duplicate rebalance date: {date.date()}")
        seen.add(date.date())
        targets.loc[matching[0]] = 0.0
        for position in decision["positions"]:
            symbol = position["symbol"]
            if symbol not in prices.columns:
                raise ValueError(f"No price for selected symbol {symbol}")
            weight = float(position["weight"])
            targets.loc[matching[0], symbol] = weight if position["direction"] == "long" else -weight
    if not seen:
        raise ValueError("No rebalance decisions were provided")

    portfolio = Portfolio.from_orders(
        close=prices, size=targets, size_type="targetpercent", direction="both",
        init_cash=initial_cash, cash_sharing=True, group_by=True, call_seq="auto",
        fees=fees_bps / 10_000, slippage=slippage_bps / 10_000, freq="1D",
    )
    equity = portfolio.value().astype("float64") / initial_cash
    strategy_returns = equity.pct_change()
    strategy_returns.iloc[0] = equity.iloc[0] - 1
    benchmark_growth = benchmark / benchmark.iloc[0]
    benchmark_returns = benchmark_growth.pct_change().fillna(0.0)
    comparison = pd.DataFrame({"strategy": equity, "spy": benchmark_growth})
    sampled = comparison.iloc[::5]
    if sampled.index[-1] != comparison.index[-1]:
        sampled = pd.concat([sampled, comparison.iloc[[-1]]])
    return {
        "strategy": _metrics(strategy_returns),
        "spy": _metrics(benchmark_returns),
        "performance_curve": [
            {"date": day.date().isoformat(), "strategy": float(row.strategy), "spy": float(row.spy)}
            for day, row in sampled.iterrows()
        ],
    }
