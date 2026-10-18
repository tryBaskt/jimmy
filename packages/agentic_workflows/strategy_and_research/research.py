"""Run a monthly price-signal backtest using only earlier prices for each signal."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

import numpy as np
import pandas as pd

from packages.agentic_workflows.strategy_and_research.plan import ResearchPlan


@dataclass(frozen=True)
class PeriodMetrics:
    total_return: float
    annualized_return: float
    sharpe: float | None
    max_drawdown: float


def _metrics(daily_returns: pd.Series) -> PeriodMetrics:
    if daily_returns.empty:
        raise ValueError("At least one trading day is required")
    equity = (1 + daily_returns).cumprod()
    total = float(equity.iloc[-1] - 1)
    annual = float(equity.iloc[-1] ** (252 / len(equity)) - 1)
    volatility = float(daily_returns.std(ddof=1))
    sharpe = (
        float(daily_returns.mean() / volatility * sqrt(252))
        if volatility > 0 and pd.notna(volatility)
        else None
    )
    drawdown = float((equity / equity.cummax() - 1).min())
    return PeriodMetrics(total, annual, sharpe, drawdown)


def _fetch_prices(
    price_client: Any, symbols: list[str], start: datetime, end: datetime
) -> pd.DataFrame:
    chunks = []
    for offset in range(0, len(symbols), 20):
        batch = symbols[offset : offset + 20]
        chunks.append(price_client.get_stock_prices_over_time(batch, start, end, "1d"))
    if not chunks:
        raise ValueError("Universe contains no symbols")
    return pd.concat(chunks, axis=1).sort_index()


def _factor_values(
    metric: str, window: pd.DataFrame, benchmark_window: pd.Series
) -> pd.Series:
    """Calculate one cross-sectional signal from prices known by signal time."""
    if metric == "trailing_return":
        return window.iloc[-1] / window.iloc[0] - 1
    if metric == "drawdown_from_peak":
        return window.iloc[-1] / window.max() - 1
    if metric == "moving_average_gap":
        return window.iloc[-1] / window.mean() - 1
    stock_returns = window.pct_change().iloc[1:]
    if metric == "realized_volatility":
        return stock_returns.std(ddof=1) * sqrt(252)
    if metric == "market_beta":
        market_returns = benchmark_window.pct_change().iloc[1:]
        market_variance = market_returns.var(ddof=1)
        if not np.isfinite(market_variance) or market_variance == 0:
            return pd.Series(np.nan, index=window.columns)
        return stock_returns.apply(lambda series: series.cov(market_returns)) / market_variance
    raise ValueError(f"Unsupported ranking factor: {metric}")


def _targets(
    prices: pd.DataFrame, benchmark: pd.Series, plan: ResearchPlan
) -> tuple[pd.DataFrame, list[dict]]:
    if plan.weighting != "equal":
        raise ValueError("Market-cap weighting needs historical market caps at each rebalance")
    # Orders execute at each month's first trading-day close. All ranking
    # prices are from the preceding close or earlier.
    targets = pd.DataFrame(float("nan"), index=prices.index, columns=prices.columns)
    selections = []
    month = prices.index.strftime("%Y-%m")
    starts = [i for i in range(1, len(prices)) if month[i] != month[i - 1]]
    for i in starts:
        prior_index = i - 1
        cutoff = prices.index[prior_index] - pd.DateOffset(months=plan.lookback_months)
        old_index = prices.index.searchsorted(cutoff, side="right") - 1
        if old_index < 0:
            continue
        window = prices.iloc[old_index : prior_index + 1]
        benchmark_window = benchmark.iloc[old_index : prior_index + 1]
        combined = pd.Series(0.0, index=prices.columns)
        valid = pd.Series(True, index=prices.columns)
        for factor in plan.factors:
            values = _factor_values(factor.metric, window, benchmark_window)
            valid &= pd.Series(np.isfinite(values), index=prices.columns)
            ranks = values.rank(
                pct=True, method="average", ascending=factor.direction == "highest"
            )
            combined += ranks.fillna(0.0) * factor.weight
        ranked = combined[valid].sort_values(ascending=False, kind="stable")
        picked = ranked.head(plan.top_n).index.tolist()
        if not picked:
            raise ValueError(f"No stocks have valid signals for {prices.index[i].date()}")
        targets.iloc[i] = 0.0
        targets.loc[prices.index[i], picked] = 1 / len(picked)
        selections.append({
            "rebalance_date": prices.index[i].date().isoformat(),
            "signal_date": prices.index[prior_index].date().isoformat(),
            "lookback_start": prices.index[old_index].date().isoformat(),
            "symbols": picked,
        })
    return targets, selections


def run_research(
    *,
    plan: ResearchPlan,
    membership_client: Any,
    price_client: Any,
    simulator: Any,
    start: datetime,
    end: datetime,
) -> dict:
    if start.tzinfo is None or end.tzinfo is None or end - start < timedelta(days=90):
        raise ValueError("Use timezone-aware dates spanning at least 90 days")
    symbols = sorted(set(membership_client.list_universe_symbols(plan.universe)))
    if not symbols:
        raise ValueError("No SP500 symbols were found in DynamoDB")
    # Earlier prices supply the first monthly signal before the requested start.
    fetch_start = (
        pd.Timestamp(start) - pd.DateOffset(months=plan.lookback_months)
        - pd.Timedelta(days=45)
    ).to_pydatetime()
    raw = _fetch_prices(price_client, symbols + ["SPY"], fetch_start, end)
    if raw.empty or "SPY" not in raw or raw["SPY"].dropna().empty:
        raise ValueError("SPY benchmark prices are unavailable")
    index = raw["SPY"].dropna().index
    raw = raw.reindex(index)
    # VectorBT requires complete prices. Record every symbol omitted here.
    eligible = [
        symbol for symbol in symbols
        if symbol in raw and raw[symbol].notna().all() and (raw[symbol] > 0).all()
    ]
    excluded = sorted(set(symbols) - set(eligible))
    if len(eligible) < plan.top_n:
        raise ValueError(
            f"Only {len(eligible)} stocks have complete prices; need {plan.top_n}"
        )
    prices = raw[eligible].astype("float64")
    spy = raw["SPY"].astype("float64")
    evaluation_index = prices.index[(prices.index >= pd.Timestamp(start)) & (prices.index < pd.Timestamp(end))]
    if len(evaluation_index) < 20:
        raise ValueError("Insufficient daily prices for a monthly backtest")
    benchmark_returns = spy.pct_change().fillna(0.0)
    exposure, selections = _targets(prices, spy, plan)
    active_selections = [
        selection for selection in selections
        if selection["rebalance_date"] >= start.date().isoformat()
    ]
    if not active_selections:
        raise ValueError("No valid monthly rebalances in the evaluation period")
    analytics = simulator.calculate_portfolio_analytics(
        prices=prices,
        target_exposure=exposure,
        frequency="1d",
        fees=plan.fees_bps / 10_000,
        slippage=plan.slippage_bps / 10_000,
        benchmark_returns=benchmark_returns,
    )
    curve = pd.Series(analytics.cumulative_returns, index=prices.index, dtype="float64")
    returns = (1 + curve).pct_change().fillna(0.0).reindex(evaluation_index)
    benchmark_evaluation = benchmark_returns.reindex(evaluation_index)
    comparison = pd.DataFrame({
        "strategy": (1 + returns).cumprod(),
        "spy": (1 + benchmark_evaluation).cumprod(),
    })
    sampled = comparison.iloc[::5]
    if sampled.index[-1] != comparison.index[-1]:
        sampled = pd.concat([sampled, comparison.iloc[[-1]]])
    return {
        "plan": asdict(plan),
        "ranking_rule": plan.ranking_rule(),
        "universe_symbol_count": len(symbols),
        "eligible_symbol_count": len(eligible),
        "excluded_symbols": excluded,
        "start": start.date().isoformat(),
        "end_exclusive": end.date().isoformat(),
        "strategy": asdict(_metrics(returns)),
        "spy": asdict(_metrics(benchmark_evaluation)),
        "selections": active_selections,
        "performance_curve": [
            {
                "date": timestamp.date().isoformat(),
                "strategy": float(row.strategy),
                "spy": float(row.spy),
            }
            for timestamp, row in sampled.iterrows()
        ],
        "limitations": [
            "Current DynamoDB membership is a present-day snapshot, not historical membership; survivorship bias makes this exploratory.",
            "Stocks lacking complete Yahoo adjusted-close history were excluded.",
            "Signals use the prior trading-day close; orders execute at the next monthly rebalance close.",
            "Each signal looks back the chosen number of calendar months; there is no separate training/holdout split.",
            "Trading costs are modeled as per-order fees and slippage; taxes, spreads beyond slippage, and market impact are omitted.",
        ],
    }
