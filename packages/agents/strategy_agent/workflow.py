"""Collect as-of-date decisions, validate them, then replay the backtest."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from packages.agent_harness.runtime import RunContext, RunRecord, ToolRegistry

from .tools.backtest import replay_portfolios
from .plan import StrategyPlan
from .feature_sources import (
    FeatureContext, FeaturePreparationContext, FeatureSourceRegistry, price_history_registry,
)
from .signal_expression import evaluate_signal

logger = logging.getLogger(__name__)


def _validate_decision(raw: Any, plan: StrategyPlan, candidates: set[str]) -> list[dict]:
    if not isinstance(raw, dict) or set(raw) != {"positions"} or not isinstance(raw["positions"], list):
        raise ValueError("The agent returned an invalid portfolio decision")
    positions = raw["positions"]
    if not 1 <= len(positions) <= plan.top_n:
        raise ValueError("The agent returned the wrong number of positions")
    seen = set()
    checked = []
    for position in positions:
        if not isinstance(position, dict) or set(position) != {"symbol", "direction", "weight", "reason"}:
            raise ValueError("A portfolio position is malformed")
        symbol, direction, weight, reason = (position[key] for key in ("symbol", "direction", "weight", "reason"))
        if not isinstance(symbol, str):
            raise ValueError("A portfolio symbol is invalid")
        if symbol not in candidates or symbol in seen:
            raise ValueError(f"The agent selected an ineligible or duplicate symbol: {symbol}")
        if direction not in ("long", "short") or (direction == "short" and not plan.allow_short):
            raise ValueError("The agent selected a disallowed trade direction")
        if type(weight) not in (int, float) or not isfinite(weight) or weight <= 0:
            raise ValueError("A portfolio weight is invalid")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError("Each position needs a short explanation")
        seen.add(symbol)
        checked.append({"symbol": symbol, "direction": direction, "weight": float(weight), "reason": reason.strip()})
    gross = sum(position["weight"] for position in checked)
    if plan.weighting == "equal":
        if abs(gross - 1) > 0.02 or any(abs(position["weight"] - 1 / len(checked)) > 0.02 for position in checked):
            raise ValueError("The agent did not return equal weights")
        for position in checked:
            position["weight"] = 1 / len(checked)
    elif plan.weighting == "model" and gross > 1 + 1e-6:
        raise ValueError("Model-suggested gross exposure exceeds 100%")
    else:
        if plan.weighting not in ("equal", "model", "market_cap"):
            raise ValueError("Unsupported weighting")
    return checked


def run_strategy(
    *,
    prompt: str,
    lookback_months: int,
    agent: Any,
    universe_loader: Callable[[str], list[str]],
    price_loader: Callable[[list[str], datetime, datetime], pd.DataFrame],
    start: datetime,
    end: datetime,
    market_cap_loader: Callable[[list[str], datetime, datetime], pd.DataFrame] | None = None,
    include_replay_prices: bool = False,
    random_seed: int | None = None,
    selected_sectors: list[str] | tuple[str, ...] = (),
    job_id: str | None = None,
    feature_sources: FeatureSourceRegistry | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict:
    """Run a price-based test while keeping each agent decision behind its cutoff."""
    if start.tzinfo is None or end.tzinfo is None or end <= start or end - start < timedelta(days=90):
        raise ValueError("Use timezone-aware dates spanning at least 90 days")
    context = RunContext(as_of=end, max_tool_calls=3 if market_cap_loader else 2)
    record = RunRecord(context.run_id)
    tools = ToolRegistry(record)
    started_at = time.monotonic()
    trace_id = job_id or context.run_id

    def progress(stage: str, **details: object) -> None:
        fields = " ".join(f"{key}={value}" for key, value in details.items())
        logger.info("Strategy job %s stage=%s elapsed_s=%.1f %s",
                    trace_id, stage, time.monotonic() - started_at, fields)

    progress("planning", lookback_months=lookback_months)

    def read_universe(_context: RunContext, arguments: dict) -> list[str]:
        if set(arguments) != {"universe"} or not isinstance(arguments["universe"], str):
            raise ValueError("Invalid universe tool arguments")
        return universe_loader(arguments["universe"])

    def read_prices(tool_context: RunContext, arguments: dict) -> pd.DataFrame:
        if set(arguments) != {"symbols", "start", "end"}:
            raise ValueError("Invalid price tool arguments")
        requested_start = datetime.fromisoformat(arguments["start"])
        requested_end = datetime.fromisoformat(arguments["end"])
        tool_context.require_available_by(requested_end)
        if requested_end <= requested_start:
            raise ValueError("Price request end must follow start")
        return price_loader(arguments["symbols"], requested_start, requested_end)

    tools.register(
        "read_universe", read_universe,
        lambda result: {"symbol_count": len(result)},
    )
    tools.register(
        "read_prices", read_prices,
        lambda result: {"rows": len(result), "symbols": len(result.columns)},
    )
    if market_cap_loader is not None:
        def read_market_caps(tool_context: RunContext, arguments: dict) -> pd.DataFrame:
            if set(arguments) != {"symbols", "start", "end"}:
                raise ValueError("Invalid market-cap tool arguments")
            requested_start = datetime.fromisoformat(arguments["start"])
            requested_end = datetime.fromisoformat(arguments["end"])
            tool_context.require_available_by(requested_end)
            return market_cap_loader(arguments["symbols"], requested_start, requested_end)

        tools.register(
            "read_market_caps", read_market_caps,
            lambda result: {"rows": len(result)},
        )
    plan = agent.plan(prompt, lookback_months)
    sources = feature_sources or price_history_registry()
    sources.require(plan.features)
    feature_definitions = {feature["id"]: feature for feature in plan.features}

    def calculate_feature(reference: str, window: pd.DataFrame,
                          benchmark: pd.Series, as_of: pd.Timestamp) -> pd.Series:
        if reference in feature_definitions:
            return sources.evaluate(
                feature_definitions[reference],
                FeatureContext(as_of=as_of, close=window, spy=benchmark,
                               evidence=evidence or {}),
            )
        return evaluate_signal(reference, window, benchmark)

    progress("plan_ready", rebalance=plan.rebalance, weighting=plan.weighting,
             holdings=plan.top_n, rule=plan.ranking_rule())
    if plan.weighting == "market_cap" and market_cap_loader is None:
        raise ValueError("Market-cap weighting requires a historical market-cap data loader")
    record.add(
        "strategy_plan", as_of=end,
        details={"model_id": getattr(agent, "model_id", "unknown"), "plan": plan.to_dict()},
    )
    progress("loading_universe", universe=plan.universe)
    symbols = sorted(set(tools.call("read_universe", context, {"universe": plan.universe})) - {"SPY"})
    progress("universe_ready", symbols=len(symbols))
    if not symbols:
        raise ValueError("The selected universe contains no stocks")
    if random_seed is not None and (type(random_seed) is not int or random_seed < 0):
        raise ValueError("random_seed must be a nonnegative integer")
    seed = random_seed if random_seed is not None else int.from_bytes(hashlib.sha256(
        json.dumps({"prompt": prompt, "plan": plan.to_dict(), "symbols": symbols,
                    "start": start.date().isoformat(), "end": end.date().isoformat()},
                   sort_keys=True).encode()
    ).digest()[:8], "big")
    rng = random.Random(seed)
    lookback_data_start = pd.Timestamp(start) - pd.DateOffset(months=plan.lookback_months)
    fetch_start = (lookback_data_start - pd.Timedelta(days=7)).to_pydatetime()
    progress("preparing_features", sources=len({feature["source"] for feature in plan.features}))
    sources.prepare(
        plan.features,
        FeaturePreparationContext(symbols=tuple(symbols), start=fetch_start,
                                  end=end, evidence=evidence or {}),
    )
    progress("loading_prices", symbols=len(symbols) + 1,
             start=fetch_start.date(), end=end.date())
    raw = tools.call(
        "read_prices", context,
        {"symbols": symbols + ["SPY"], "start": fetch_start.isoformat(), "end": end.isoformat()},
    )
    raw = raw.loc[(raw.index >= fetch_start) & (raw.index < end)]
    if raw.empty or "SPY" not in raw or raw["SPY"].dropna().empty:
        raise ValueError("SPY benchmark prices are unavailable")
    raw = raw.reindex(raw["SPY"].dropna().index).sort_index()
    eligible = [
        symbol for symbol in symbols
        if symbol in raw and raw[symbol].notna().all() and (raw[symbol] > 0).all()
    ]
    excluded = sorted(set(symbols) - set(eligible))
    if len(eligible) < plan.top_n:
        raise ValueError(f"Only {len(eligible)} stocks have complete prices; need {plan.top_n}")
    progress("prices_ready", trading_days=len(raw), eligible=len(eligible), excluded=len(excluded))
    prices = raw[eligible].astype("float64")
    spy = raw["SPY"].astype("float64")
    period = prices.index.tz_convert("UTC").tz_localize(None).to_period(
        "M" if plan.rebalance == "monthly" else "Q"
    )
    boundaries = [i for i in range(1, len(prices)) if period[i] != period[i - 1]]
    decisions = []
    for i in boundaries:
        execute_date = prices.index[i]
        if execute_date < start or execute_date >= end:
            continue
        signal_date = prices.index[i - 1]
        sample_only = plan.selection_steps == ({"operation": "sample"},)
        first = None
        if not sample_only:
            cutoff = signal_date - pd.DateOffset(months=plan.lookback_months)
            first = prices.index.searchsorted(cutoff, side="right") - 1
            if first < 0:
                continue
        progress("rebalance_start", number=len(decisions) + 1,
                 signal_date=signal_date.date(), execute_date=execute_date.date())
        if plan.selection_steps:
            candidates_symbols = eligible.copy()
            feature_cache = {}
            def feature_values(feature: str) -> pd.Series:
                if feature not in feature_cache:
                    values = calculate_feature(feature, prices.iloc[first:i], spy.iloc[first:i], signal_date)
                    feature_cache[feature] = values.replace([np.inf, -np.inf], np.nan).dropna()
                return feature_cache[feature]
            for step in plan.selection_steps:
                if step["operation"] == "filter":
                    values = feature_values(step["feature"])
                    compare = {"gt": values.gt, "gte": values.ge, "lt": values.lt, "lte": values.le}[step["comparison"]]
                    passing = compare(step["value"])
                    candidates_symbols = [symbol for symbol in candidates_symbols if bool(passing.get(symbol, False))]
                elif step["operation"] == "rank":
                    values = feature_values(step["feature"])
                    candidates_symbols = [symbol for symbol in candidates_symbols if symbol in values]
                    candidates_symbols.sort(key=lambda symbol: (float(values[symbol]), symbol),
                                            reverse=step["direction"] == "highest")
                    candidates_symbols = candidates_symbols[:plan.top_n]
                else:
                    if len(candidates_symbols) < plan.top_n:
                        raise ValueError(f"Only {len(candidates_symbols)} stocks pass the selection steps on {signal_date.date()}")
                    candidates_symbols = rng.sample(candidates_symbols, plan.top_n)
            if len(candidates_symbols) < plan.top_n:
                raise ValueError(f"Only {len(candidates_symbols)} stocks pass the selection steps on {signal_date.date()}")
            candidates = [{"symbol": symbol} for symbol in candidates_symbols]
            raw_decision = {"positions": [
                {"symbol": symbol, "direction": "long", "weight": 1 / plan.top_n,
                 "reason": plan.ranking_rule()}
                for symbol in candidates_symbols
            ]}
            positions = _validate_decision(raw_decision, plan, set(candidates_symbols))
        else:
            values = calculate_feature(plan.metric, prices.iloc[first:i], spy.iloc[first:i], signal_date)
            values = values.replace([np.inf, -np.inf], np.nan).dropna()
            if len(values) < plan.top_n:
                raise ValueError(f"Too few valid signals on {signal_date.date()}")
            ranked = values.sort_values(ascending=plan.rank_direction == "lowest", kind="stable")
            shortlist = ranked.head(max(60, plan.top_n * 3))
            candidates = [{"symbol": symbol, "signal": round(float(value), 8)} for symbol, value in shortlist.items()]
            progress("decision_model_start", signal_date=signal_date.date(),
                     candidates=len(candidates))
            raw_decision = agent.decide(plan, signal_date.date().isoformat(), candidates)
            positions = _validate_decision(raw_decision, plan, set(shortlist.index))
        record.add(
            "portfolio_decision", as_of=signal_date.to_pydatetime(),
            details={
                "signal_date": signal_date.date().isoformat(),
                "rebalance_date": execute_date.date().isoformat(),
                "candidates": candidates,
                "model_positions": positions,
            },
        )
        decisions.append({
            "signal_date": signal_date.date().isoformat(),
            "rebalance_date": execute_date.date().isoformat(),
            "lookback_start": prices.index[first].date().isoformat() if first is not None else None,
            "positions": positions,
            "symbols": [position["symbol"] for position in positions],
            "candidate_count": len(candidates),
        })
        progress("rebalance_ready", number=len(decisions), candidates=len(candidates),
                 positions=len(positions))
    if not decisions:
        raise ValueError("No valid rebalance dates in the selected period")
    progress("decisions_ready", rebalances=len(decisions))
    market_cap_digest = None
    used_market_cap_fallback = False
    if plan.weighting == "market_cap":
        selected = sorted({
            position["symbol"] for decision in decisions for position in decision["positions"]
        })
        progress("loading_market_caps", symbols=len(selected))
        caps = tools.call(
            "read_market_caps", context,
            {"symbols": selected, "start": fetch_start.isoformat(), "end": end.isoformat()},
        )
        if (
            not isinstance(caps, pd.DataFrame)
            or not isinstance(caps.index, pd.MultiIndex)
            or caps.index.names != ["timestamp", "symbol"]
            or "market_cap" not in caps
        ):
            raise ValueError("Historical market-cap data has an invalid shape")
        caps = caps.sort_index()
        market_cap_digest = hashlib.sha256(
            caps[["market_cap"]].to_csv(float_format="%.12g").encode()
        ).hexdigest()
        available_symbols = set(caps.index.get_level_values("symbol"))
        fallback_positions = 0
        for decision in decisions:
            signal_day = pd.Timestamp(decision["signal_date"]).date()
            selected_caps = []
            for position in decision["positions"]:
                symbol = position["symbol"]
                cap = None
                if symbol in available_symbols:
                    history = caps.xs(symbol, level="symbol")["market_cap"]
                    same_day = history.loc[history.index.date == signal_day]
                    if not same_day.empty:
                        value = pd.to_numeric(same_day.iloc[-1], errors="coerce")
                        if isfinite(value) and value > 0:
                            cap = float(value)
                selected_caps.append(cap)
            missing_count = sum(cap is None for cap in selected_caps)
            fallback_positions += missing_count
            used_market_cap_fallback |= missing_count > 0
            fallback_weight = 1 / len(selected_caps)
            remaining_weight = 1 - missing_count * fallback_weight
            total_known_cap = sum(cap for cap in selected_caps if cap is not None)
            for position, cap in zip(decision["positions"], selected_caps):
                position["market_cap"] = cap
                position["weight"] = (
                    remaining_weight * cap / total_known_cap
                    if cap is not None else fallback_weight
                )
            record.add(
                "market_cap_weights",
                as_of=pd.Timestamp(decision["signal_date"], tz="UTC").to_pydatetime(),
                details={
                    "signal_date": decision["signal_date"],
                    "positions": decision["positions"],
                    "fallback_symbols": [
                        position["symbol"] for position, cap in zip(decision["positions"], selected_caps)
                        if cap is None
                    ],
                },
            )
        progress("market_caps_ready", rows=len(caps), fallback_positions=fallback_positions)
    evaluation = prices.index[(prices.index >= start) & (prices.index < end)]
    if len(evaluation) < 20:
        raise ValueError("Insufficient trading days for a backtest")
    used_symbols = sorted({position["symbol"] for decision in decisions for position in decision["positions"]})
    replay_prices = prices.loc[evaluation, used_symbols].copy()
    replay_spy = spy.loc[evaluation].copy()
    progress("backtest_start", trading_days=len(evaluation), symbols=len(used_symbols),
             rebalances=len(decisions))
    simulation = replay_portfolios(
        replay_prices, replay_spy, decisions,
        fees_bps=plan.fees_bps, slippage_bps=plan.slippage_bps,
    )
    progress("backtest_ready", curve_points=len(simulation["performance_curve"]))
    snapshot = pd.concat([replay_prices, replay_spy.rename("SPY")], axis=1)
    digest = hashlib.sha256(snapshot.to_csv(float_format="%.12g").encode()).hexdigest()
    record.add("price_snapshot", as_of=end, details={"sha256": digest})
    if market_cap_digest is not None:
        record.add("market_cap_snapshot", as_of=end, details={"sha256": market_cap_digest})
    report = {
        "run_record": record.to_dict(),
        "plan": plan.to_dict(), "ranking_rule": plan.ranking_rule(),
        "model_id": getattr(agent, "model_id", "unknown"),
        "random_seed": seed if any(step["operation"] == "sample" for step in plan.selection_steps) else None,
        "universe_symbol_count": len(symbols), "eligible_symbol_count": len(eligible),
        "selected_sectors": sorted(set(selected_sectors)),
        "excluded_symbols": excluded,
        "lookback_data_start": lookback_data_start.date().isoformat(),
        "start": start.date().isoformat(), "end_exclusive": end.date().isoformat(),
        "decisions": decisions, "selections": decisions,
        "price_data_sha256": digest,
        "market_cap_data_sha256": market_cap_digest,
        "limitations": [
            "Universe membership and sector filters use the latest stored snapshots; historical tests retain survivorship and classification bias.",
            "Yahoo adjusted closes approximate dividend-inclusive returns; execution uses the next trading-day close, not a real traded adjusted price.",
            "Stocks missing complete prices over the fetched period were excluded.",
            "The model assessed only signals available at each prior close; its decisions may differ on a new run.",
            *(
                ["Market-cap weights use Yahoo share-count history, whose original publication times are not verified."]
                if plan.weighting == "market_cap" else []
            ),
            *(
                ["Selected stocks without a valid market cap received equal-weight slots; stocks with valid caps split the remaining weight proportionally."]
                if used_market_cap_fallback else []
            ),
        ],
        **simulation,
    }
    if include_replay_prices:
        report["replay_prices"] = [
            {"date": day.date().isoformat(), **{symbol: float(value) for symbol, value in row.items()}}
            for day, row in snapshot.iterrows()
        ]
    json.dumps(report, allow_nan=False)
    progress("report_ready", rebalances=len(decisions))
    return report
