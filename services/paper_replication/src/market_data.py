"""Read actual observations, preserve gaps, and save acquisitions locally."""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from tools.yahoo_finance import YFinanceClient

from models import DatasetSpec, ExperimentSpec


def monthly_returns(prices: pd.DataFrame, end) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Exclude the incomplete ending month. Never forward-fill observations.
    cutoff = min(pd.Timestamp(end), pd.Timestamp.today().normalize()).to_period("M").start_time
    prices = prices.loc[prices.index < cutoff]
    grouped = prices.resample("ME")
    closes = grouped.last()
    closes = closes.where(grouped.count().eq(grouped.size(), axis=0))
    return closes.pct_change(fill_method=None), closes


def validate_returns(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "security_id", "return"}
    if not required.issubset(frame.columns):
        raise ValueError("CSV must contain date,security_id,return (decimal monthly returns)")
    frame = frame[list(sorted(required))].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.to_period("M").dt.to_timestamp("M")
    frame["return"] = pd.to_numeric(frame["return"], errors="raise")
    if frame.empty or frame.isna().any().any() or not np.isfinite(frame["return"]).all():
        raise ValueError("CSV contains empty, missing, or nonfinite observations")
    if (frame["return"] < -1).any() or frame.duplicated(["date", "security_id"]).any():
        raise ValueError("CSV has returns below -100% or duplicate security-month rows")
    frame["security_id"] = frame["security_id"].astype(str)
    matrix = frame.pivot(index="date", columns="security_id", values="return").sort_index()
    return matrix.reindex(pd.date_range(matrix.index.min(), matrix.index.max(), freq="ME"))


def fetch_csv(url: str) -> bytes:
    from ipaddress import ip_address
    from socket import getaddrinfo
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Data URL must be a public HTTPS URL without credentials")
    if parsed.port not in (None, 443):
        raise ValueError("Only HTTPS port 443 is supported")
    addresses = getaddrinfo(parsed.hostname, 443)
    if not addresses or any(not ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Data URL must resolve to a public host")
    with httpx.stream("GET", url, timeout=30, follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 25_000_000:
                raise ValueError("Public CSV exceeds the 25 MB local limit")
    return bytes(body)


def acquire(spec: DatasetSpec, folder: Path, local_csv: Path | None = None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    provenance = {
        "requested": spec.model_dump(mode="json"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source": "local_csv" if local_csv else spec.source,
        "verified_available": False,
    }
    try:
        if local_csv or spec.source == "public_csv":
            body = local_csv.read_bytes() if local_csv else fetch_csv(spec.csv_url)
            (folder / "source.csv").write_bytes(body)
            returns = validate_returns(pd.read_csv(BytesIO(body)))
            if spec.symbols:
                returns = returns.reindex(columns=spec.symbols)
            provenance["sha256"] = sha256(body).hexdigest()
            provenance["limitations"] = [
                "CSV units, historical membership and source methodology need manual verification."
            ]
        else:
            daily = YFinanceClient().get_stock_prices_over_time(spec.symbols, spec.start, spec.end)
            body = daily.to_csv().encode()
            (folder / "daily_adjusted_close.csv").write_bytes(body)
            returns, closes = monthly_returns(daily, spec.end)
            closes.to_csv(folder / "monthly_adjusted_close.csv", index_label="date")
            provenance["sha256"] = sha256(body).hexdigest()
            provenance["limitations"] = [
                "Yahoo adjusted prices are a total-return proxy, not CRSP returns.",
                "Historical membership, delistings and point-in-time identifiers are unverified.",
                "A current ticker universe has survivorship and selection bias.",
                "Incomplete asset-months are missing; no imputation or forward filling is used.",
            ]
        returns = returns.loc[(returns.index >= pd.Timestamp(spec.start)) &
                              (returns.index < pd.Timestamp(spec.end))]
        returns.to_csv(folder / "monthly_returns.csv", index_label="date")
        coverage = {}
        for symbol in returns.columns:
            observed = returns[symbol].dropna()
            coverage[str(symbol)] = {
                "months": len(observed),
                "first": str(observed.index.min().date()) if len(observed) else None,
                "last": str(observed.index.max().date()) if len(observed) else None,
                "missing_months": int(returns[symbol].isna().sum()),
            }
        provenance.update(
            verified_available=bool(returns.notna().any().any()), coverage=coverage, months=len(returns),
            returns_path=str(folder / "monthly_returns.csv"),
        )
    except Exception as error:
        provenance["error"] = f"{type(error).__name__}: {error}"
    return provenance


def read_returns(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.columns = frame.columns.astype(str)
    return frame


def quality_check(acquisition: dict, experiments: list[ExperimentSpec], *, check_evaluation_dates=False) -> dict:
    issues = []
    if not acquisition.get("verified_available"):
        return {"usable": False, "issues": [acquisition.get("error", "No observed data")]}
    matrix = read_returns(acquisition["returns_path"])
    supported = [e for e in experiments if e.method != "unsupported"]
    if not supported:
        issues.append("No requested experiment has an implemented method")
    complete = matrix.notna().all(axis=1)
    groups = (~complete).cumsum()
    runs = complete.groupby(groups).sum()
    window = matrix.index[complete & (groups == runs.idxmax())] if len(runs) else matrix.index[:0]
    for experiment in supported:
        minimum = experiment.quantiles if experiment.method == "cross_sectional_momentum" else 1
        warmup = (0 if experiment.method == "buy_and_hold"
                  else experiment.formation_months + experiment.skip_months + experiment.holding_months - 1)
        needed = warmup + 12
        if matrix.shape[1] < minimum or len(window) < needed:
            issues.append(f"{experiment.id}: need {minimum} assets and {needed} contiguous complete months; "
                          f"got {matrix.shape[1]} assets and {len(window)} months")
        elif check_evaluation_dates:
            eligible = window[warmup:]
            if experiment.start:
                eligible = eligible[eligible >= pd.Timestamp(experiment.start)]
            if experiment.end:
                eligible = eligible[eligible < pd.Timestamp(experiment.end)]
            if len(eligible) < 12:
                issues.append(f"{experiment.id}: fewer than 12 months in the selected evaluation period after warmup")
    return {"usable": not issues, "issues": issues,
            "common_start": str(window[0].date()) if len(window) else None,
            "common_end": str(window[-1].date()) if len(window) else None,
            "sample_rule": "Longest contiguous complete common window, selected without observing strategy results"}
