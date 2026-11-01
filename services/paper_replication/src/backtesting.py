"""Fixed numerical methods. Agents select parameters, never executable code."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from models import ExperimentSpec
from market_data import read_returns


def build_weights(returns: pd.DataFrame, spec: ExperimentSpec) -> pd.DataFrame:
    """Month t weights use returns strictly before t and before the skip window."""
    if spec.method == "unsupported":
        raise ValueError("Method not implemented; see the plan's deviations")
    weights = pd.DataFrame(np.nan, index=returns.index, columns=returns.columns)
    j, k, skip = spec.formation_months, spec.holding_months, spec.skip_months
    cohorts = []
    for t in range(len(returns)):
        if spec.method == "buy_and_hold":
            weights.iloc[t] = 1.0 / returns.shape[1]
            continue
        if t < j + skip:
            continue
        history = returns.iloc[t - skip - j:t - skip]
        valid = history.columns[history.notna().all()]
        score = (1 + history[valid]).prod() - 1
        cohort = pd.Series(0.0, index=returns.columns)
        if spec.method == "cross_sectional_momentum":
            count = len(valid) // spec.quantiles
            if count < 1 or score.nunique() < 2:
                cohorts = []
                continue
            ranking = score.sort_index().sort_values(kind="stable")
            if ranking.iloc[count - 1] >= ranking.iloc[-count]:
                cohorts = []
                continue
            cohort.loc[ranking.index[-count:]] = 1.0 / count
            cohort.loc[ranking.index[:count]] = -1.0 / count
        else:
            if not len(valid):
                cohorts = []
                continue
            cohort.loc[valid] = np.sign(score) / len(valid)
        cohorts = (cohorts + [cohort])[-k:]
        if len(cohorts) == k:
            weights.iloc[t] = pd.concat(cohorts, axis=1).mean(axis=1)
    return weights


def evaluate(returns: pd.DataFrame, weights: pd.DataFrame, spec: ExperimentSpec):
    active = weights.notna().all(axis=1)
    if spec.start:
        active &= returns.index >= pd.Timestamp(spec.start)
    if spec.end:
        active &= returns.index < pd.Timestamp(spec.end)
    held_missing = (weights.fillna(0).ne(0) & returns.isna()).any(axis=1)
    if (active & held_missing).any():
        raise ValueError("Held-security return missing; cannot assume zero or redistribute holdings")
    dates = returns.index[active]
    if len(dates) < 12:
        raise ValueError("At least 12 evaluated months are required")
    if len(pd.date_range(dates.min(), dates.max(), freq="ME")) != len(dates):
        raise ValueError("Evaluation has internal gaps; choose an explicit contiguous sample")
    gross = (weights.loc[active] * returns.loc[active].fillna(0)).sum(axis=1)
    if spec.method == "buy_and_hold":
        growth = (1 + returns.loc[active]).cumprod()
        wealth = growth.mean(axis=1)
        gross = wealth.pct_change(fill_method=None)
        gross.iloc[0] = returns.loc[active].iloc[0].mean()
        beginning = growth.shift(1, fill_value=1)
        weights = weights.copy()
        weights.loc[active] = beginning.div(beginning.sum(axis=1), axis=0)
    realized = weights.loc[active]
    drifted = realized.shift(1).fillna(0).mul(1 + returns.loc[active].shift(1).fillna(0))
    drifted = drifted.div(1 + gross.shift(1).fillna(0), axis=0)
    turnover = (realized - drifted).abs().sum(axis=1)
    cost = turnover * (spec.fees_bps + spec.slippage_bps) / 10_000
    net = gross - cost
    return pd.DataFrame({"gross_return": gross, "estimated_net_return": net,
                         "turnover": turnover, "estimated_cost": cost}), realized


def summarize(series: pd.Series, lags: int) -> dict:
    fit = sm.OLS(series.to_numpy(), np.ones((len(series), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": min(lags, len(series) - 1)},
    )
    std = series.std(ddof=1)
    wealth = pd.concat([pd.Series([1.0]), (1 + series).cumprod().reset_index(drop=True)])
    return {
        "months": len(series), "mean_monthly_return": float(series.mean()),
        "hac_t_stat": float(fit.tvalues[0]) if np.isfinite(fit.tvalues[0]) else None,
        "hac_standard_error": float(fit.bse[0]), "hac_lags": min(lags, len(series) - 1),
        "annualized_sharpe": float(series.mean() / std * math.sqrt(12)) if std > 0 else None,
        "annualized_volatility": float(std * math.sqrt(12)),
        "hypothetical_total_return": float((1 + series).prod() - 1),
        "max_drawdown": float((wealth / wealth.cummax() - 1).min()),
    }


class VectorBTClient:
    def calculate_portfolio_analytics(self, *, prices, target_exposure, fees=0.0, slippage=0.0,
                                      initial_cash=100_000.0, buy_and_hold=False):
        from vectorbt import Portfolio

        if not prices.index.equals(target_exposure.index) or not prices.columns.equals(target_exposure.columns):
            raise ValueError("Prices and exposures must have identical axes")
        if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
            raise ValueError("VectorBT requires complete positive prices")
        if initial_cash <= 0 or fees < 0 or slippage < 0:
            raise ValueError("Invalid cash, fees, or slippage")
        orders = target_exposure.copy()
        if buy_and_hold:
            orders.iloc[1:] = np.nan
        portfolio = Portfolio.from_orders(
            close=prices, size=orders, size_type="targetpercent", direction="both",
            init_cash=initial_cash, cash_sharing=True, group_by=True, call_seq="auto",
            fees=fees, slippage=slippage, freq="30D", raise_reject=True,
        )
        value = portfolio.value()
        return {
            "final_account_return": float(value.iloc[-1] / initial_cash - 1),
            "filled_orders": len(portfolio.orders.records),
            "note": "Monthly-close execution diagnostic including configured fees and slippage. "
                    "No borrow fees, financing constraints or market impact. Not the paper's gross spread.",
        }


def run_experiment(spec: ExperimentSpec, acquisition: dict, folder: Path) -> dict:
    result = {"id": spec.id, "spec": spec.model_dump(mode="json"), "status": "failed"}
    try:
        matrix = read_returns(acquisition["returns_path"])
        if acquisition.get("evaluation_window"):
            start, end = acquisition["evaluation_window"]
            matrix = matrix.loc[start:end]
        weights = build_weights(matrix, spec)
        series, weights = evaluate(matrix, weights, spec)
        folder.mkdir(parents=True, exist_ok=True)
        series.to_csv(folder / "returns.csv", index_label="date")
        weights.to_csv(folder / "weights.csv", index_label="date")
        metrics = summarize(series.gross_return, spec.holding_months - 1)
        net_metrics = summarize(series.estimated_net_return, spec.holding_months - 1)
        result.update(status="completed", metrics=metrics, estimated_net_metrics=net_metrics,
                      start=str(series.index.min().date()), end=str(series.index.max().date()),
                      mean_turnover=float(series.turnover.mean()), artifact_dir=str(folder))
        result["comparisons"] = [
            {"metric": expected.metric, "published": expected.value,
             "observed": metrics.get(expected.metric), "tolerance": expected.tolerance,
             "difference": (metrics[expected.metric] - expected.value)
             if metrics.get(expected.metric) is not None else None,
             "citation": expected.citation.model_dump(), "outcome": "not_comparable",
             "reason": "Dataset provenance and exact paper methodology are not independently verified."}
            for expected in spec.published
        ]
        window = matrix.loc[series.index]
        if window.isna().any().any() or (window <= -1).any().any():
            raise ValueError("VectorBT diagnostic needs complete returns greater than -100%")
        prior = series.index[0] - pd.offsets.MonthEnd(1)
        prices = pd.concat([pd.DataFrame(1.0, index=[prior], columns=window.columns),
                            (1 + window).cumprod()])
        # Execute t's target at the preceding close, then observe t's return.
        targets = weights.copy()
        targets.index = prices.index[:-1]
        targets = targets.reindex(prices.index)
        result["vectorbt"] = VectorBTClient().calculate_portfolio_analytics(
            prices=prices, target_exposure=targets,
            fees=spec.fees_bps / 10_000, slippage=spec.slippage_bps / 10_000,
            buy_and_hold=spec.method == "buy_and_hold",
        )
    except Exception as error:
        key = "vectorbt_error" if result["status"] == "completed" else "error"
        result[key] = f"{type(error).__name__}: {error}"
    return result
