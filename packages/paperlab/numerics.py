"""Trusted numerical templates. Never executes model-generated code."""

from io import BytesIO
import numpy as np
import pandas as pd
import statsmodels.api as sm
from paperlab.models import Experiment


TEMPLATE_VERSION = "monthly-equal-weight-overlap-v1"


def validate_returns(body):
    frame = pd.read_csv(BytesIO(body))
    required = {"date", "security_id", "return"}
    if not required.issubset(frame.columns):
        raise ValueError("CSV requires date,security_id,return columns")
    frame = frame[list(sorted(required))].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.to_period("M").dt.to_timestamp("M")
    frame["return"] = pd.to_numeric(frame["return"], errors="raise")
    if frame.empty or frame["security_id"].isna().any() or not np.isfinite(frame["return"]).all():
        raise ValueError("Empty, missing or non-finite observations")
    if (frame["return"] < -1).any() or frame.duplicated(["date", "security_id"]).any():
        raise ValueError("Invalid returns or duplicate security-month observations")
    return frame.sort_values(["date", "security_id"])


def summarize(series, lags=0):
    series = series.dropna()
    if len(series) < 3:
        raise ValueError("At least three evaluated months are required")
    fit = sm.OLS(series.to_numpy(), np.ones((len(series), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": min(lags, len(series) - 1)}
    )
    wealth = pd.concat([pd.Series([1.0]), (1 + series).cumprod().reset_index(drop=True)], ignore_index=True)
    drawdown = wealth / wealth.cummax() - 1
    t_stat = float(fit.tvalues[0]) if np.isfinite(fit.tvalues[0]) else None
    return {
        "mean_monthly_return": float(series.mean()),
        "t_stat": t_stat,
        "annualized_volatility": float(series.std(ddof=1) * np.sqrt(12)),
        "max_drawdown": float(drawdown.min()),
        "months": len(series),
        "standard_error": float(fit.bse[0]),
        "hac_lags": min(lags, len(series) - 1),
        "wealth_convention": "hypothetical compounded long-short spread; per dollar long, before costs",
    }


def execute(body: bytes, experiment: Experiment):
    if experiment.method == "unsupported":
        raise ValueError("Unsupported experiment method")
    data = validate_returns(body)
    if experiment.requirement.symbols:
        data = data[data.security_id.isin(experiment.requirement.symbols)]
        absent = set(experiment.requirement.symbols) - set(data.security_id)
        if absent:
            raise ValueError("Requested security identifiers are missing from the selected snapshot")
    if data.empty:
        raise ValueError("No observations match the requested security identifiers")
    matrix = data.pivot(index="date", columns="security_id", values="return")
    matrix = matrix.reindex(pd.date_range(matrix.index.min(), matrix.index.max(), freq="ME"))
    start, end = pd.Timestamp(experiment.requirement.start), pd.Timestamp(experiment.requirement.end)
    holdings, records = [], []
    if experiment.method == "return_series":
        if matrix.shape[1] != 1:
            raise ValueError("Return-series template requires one explicitly identified series")
        evaluated = matrix.loc[(matrix.index >= start) & (matrix.index <= end)].iloc[:, 0].dropna()
    else:
        # At month t rank only fully observed returns ending at t-1-skip; no future data.
        j, k, skip = experiment.formation_months, experiment.holding_months, experiment.skip_months
        for t, date in enumerate(matrix.index):
            cohorts = []
            for formed in range(max(j + skip, t - k + 1), t + 1):
                history = matrix.iloc[formed - skip - j : formed - skip]
                valid = history.columns[history.notna().all()]
                count = len(valid) // experiment.quantiles
                if count < 1:
                    continue
                ranking = ((1 + history[valid]).prod() - 1).sort_values(kind="stable")
                losers, winners = list(ranking.index[:count]), list(ranking.index[-count:])
                current = matrix.iloc[t]
                # Do not silently drop delisted/missing held securities and redistribute weights.
                if current[winners + losers].isna().any():
                    raise ValueError("Held-security return missing; require explicit delisting treatment")
                spread = float(current[winners].mean() - current[losers].mean())
                cohorts.append(spread)
                if date >= start and date <= end:
                    holdings.append(
                        {
                            "date": str(date.date()),
                            "formed": str(matrix.index[formed].date()),
                            "winners": winners,
                            "losers": losers,
                        }
                    )
            if len(cohorts) == k and start <= date <= end:
                records.append((date, float(np.mean(cohorts))))
        evaluated = pd.Series(dict(records), dtype=float)
    metrics = summarize(evaluated, experiment.holding_months - 1)
    return {
        "template_version": TEMPLATE_VERSION,
        "metrics": metrics,
        "series": [{"date": str(d.date()), "return": float(v)} for d, v in evaluated.items()],
        "holdings": holdings,
        "sample": {"start": str(evaluated.index.min().date()), "end": str(evaluated.index.max().date())},
        "caveats": [
            "Before transaction, financing, shorting and market-impact costs.",
            "HAC statistics may differ from the paper's estimator; comparison requires matching conventions.",
        ],
    }


def compare(result, experiment, eligible):
    comparisons = []
    for expected in experiment.expected:
        actual = result["metrics"].get(expected.metric)
        meaningful = eligible and actual is not None
        comparisons.append(
            {
                "metric": expected.metric,
                "published": expected.value,
                "calculated": actual,
                "difference": actual - expected.value if actual is not None else None,
                "tolerance": expected.tolerance,
                "citation": expected.citation.model_dump(),
                "outcome": (
                    "within_tolerance"
                    if abs(actual - expected.value) <= expected.tolerance
                    else "outside_tolerance"
                )
                if meaningful
                else "not_comparable",
            }
        )
    return comparisons
