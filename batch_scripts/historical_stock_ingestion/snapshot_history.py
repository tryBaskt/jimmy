"""Prepare auditable observed membership intervals without inventing identities.

CSV symbols are source labels, not necessarily historically valid exchange tickers.
Only a verified registry may link different labels or noncontiguous appearances.
No classification data or external dependencies are used here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class Snapshot:
    observed_at: str
    symbols: frozenset[str]


def _date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"Date must use YYYY-MM-DD: {value!r}")
    return value


def read_snapshots(path: str | Path) -> list[Snapshot]:
    """Reject malformed, duplicate or unordered input rather than silently repair it."""
    snapshots = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["date", "tickers"]:
            raise ValueError("Expected exactly the CSV columns date,tickers")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV record at line {reader.line_num}")
            observed_at = _date(row["date"])
            if snapshots and observed_at <= snapshots[-1].observed_at:
                raise ValueError(f"Duplicate or unordered snapshot: {observed_at}")
            symbols = row["tickers"].split(",")
            if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.^/-]*", s) for s in symbols):
                raise ValueError(f"Invalid or empty symbol at {observed_at}")
            if len(set(symbols)) != len(symbols):
                raise ValueError(f"Duplicate symbol at {observed_at}")
            snapshots.append(Snapshot(observed_at, frozenset(symbols)))
    if not snapshots:
        raise ValueError("CSV has no snapshots")
    return snapshots


def symbol_runs(snapshots: list[Snapshot]) -> list[dict]:
    """Return half-open observation intervals with both sides of uncertain bounds."""
    active: dict[str, dict] = {}
    result = []
    previous = None
    for snapshot in snapshots:
        if previous is not None and snapshot.observed_at <= previous:
            raise ValueError("Snapshots must be strictly increasing")
        for symbol in sorted(set(active) - snapshot.symbols):
            result.append({**active.pop(symbol), "removed_at": snapshot.observed_at,
                           "removal_effective_date_known": False})
        for symbol in sorted(snapshot.symbols):
            if symbol not in active:
                active[symbol] = {
                    "symbol": symbol, "added_at": snapshot.observed_at,
                    "observed_from": snapshot.observed_at,
                    "addition_effective_date_known": False,
                    "left_censored": previous is None,
                }
                if previous is not None:
                    active[symbol]["previous_absent_at"] = previous
            active[symbol]["observed_through"] = snapshot.observed_at
        previous = snapshot.observed_at
    result.extend({**run, "right_censored": True} for run in active.values())
    return sorted(result, key=lambda run: (run["symbol"], run["added_at"]))


def prepare_history(path: str | Path, registry: list[dict] | None = None,
                    universe: str = "SP500", dataset: str = "sp500-historical-components") -> dict:
    """Build three-table inputs; GICS intentionally remains empty pending evidence.

    Registry records identify a source symbol run with ``symbol`` and
    ``observed_from``. Each requires ``security_id``, ``verified=True`` and a
    nonempty ``evidence`` string. Optional ``ticker_aliases`` must carry confirmed
    effective_from/effective_to bounds; they are never inferred from snapshots.
    An unresolved ID represents a source run, not a confirmed company identity.
    """
    path = Path(path)
    snapshots = read_snapshots(path)
    runs = symbol_runs(snapshots)
    run_keys = {(r["symbol"], r["observed_from"]) for r in runs}
    verified = {}
    for record in registry or []:
        key = (record["symbol"], record["observed_from"])
        if key not in run_keys or key in verified:
            raise ValueError(f"Unknown or duplicate registry run: {key}")
        if record.get("verified") is not True or not record.get("evidence") or not record.get("security_id"):
            raise ValueError(f"Registry identity requires verified evidence: {key}")
        aliases = record.get("ticker_aliases", [])
        for alias in aliases:
            if not alias.get("symbol") or not alias.get("evidence"):
                raise ValueError("Ticker aliases require symbol and evidence")
            start = _date(alias["effective_from"])
            if alias.get("effective_to") and _date(alias["effective_to"]) <= start:
                raise ValueError("Ticker alias end must follow start")
        verified[key] = record
    source = {"dataset": dataset, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    metadata = {}
    membership = []
    counts = Counter(run["symbol"] for run in runs)
    for run in runs:
        key = (run["symbol"], run["observed_from"])
        identity = verified.get(key)
        security_id = (identity["security_id"] if identity else
                       "unresolved_" + str(uuid5(NAMESPACE_URL, f"jimmy/{dataset}/{key[0]}/{key[1]}")))
        item = metadata.setdefault(security_id, {
            "security_id": security_id,
            "identity_status": "verified" if identity else "unresolved",
            "source_symbol_observations": [], "ticker_aliases": [],
        })
        item["source_symbol_observations"].append({
            k: run[k] for k in ("symbol", "observed_from", "observed_through")
        })
        if run["observed_through"] >= item.get("observed_through", ""):
            item["symbol"] = run["symbol"]
            item["symbol_basis"] = "source_snapshot_label"
            item["observed_through"] = run["observed_through"]
        item["observed_from"] = min(item.get("observed_from", run["observed_from"]), run["observed_from"])
        if identity:
            for field in ("share_class", "source_label_notes"):
                if identity.get(field):
                    if item.get(field) and item[field] != identity[field]:
                        raise ValueError(f"Conflicting registry {field} for {security_id}")
                    item[field] = identity[field]
            if identity.get("company_name"):
                if item.get("company_name") and item["company_name"] != identity["company_name"]:
                    raise ValueError(f"Conflicting registry company names for {security_id}")
                item["company_name"] = identity["company_name"]
            item.setdefault("identity_evidence", []).append(identity["evidence"])
            for alias in identity.get("ticker_aliases", []):
                if alias not in item["ticker_aliases"]:
                    item["ticker_aliases"].append(alias)
        membership_run = {k: v for k, v in run.items() if k != "symbol"}
        membership.append({**membership_run, "source_symbol": run["symbol"],
                           "security_id": security_id, "universe": universe,
                           "membership_key": f"{security_id}#{run['added_at']}",
                           "dataset_observed_from": snapshots[0].observed_at,
                           "dataset_observed_through": snapshots[-1].observed_at,
                           "data_coverage_start": snapshots[0].observed_at,
                           "data_coverage_end": snapshots[-1].observed_at,
                           "date_basis": "snapshot_observation", "source": source})
    # Multiple source labels for one confirmed security must not silently double
    # count membership. Merging them requires a separate explicit interval step.
    by_security = {}
    for record in membership:
        by_security.setdefault(record["security_id"], []).append(record)
    for security_id, records in by_security.items():
        ordered = sorted(records, key=lambda r: r["added_at"])
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.get("removed_at", "9999-12-31") > later["added_at"]:
                raise ValueError(f"Overlapping source membership for {security_id}; reconcile registry")
    coalesced = []
    for security_id, records in sorted(by_security.items()):
        for record in sorted(records, key=lambda r: r["added_at"]):
            observation = {"symbol": record["source_symbol"], "observed_from": record["observed_from"],
                           "observed_through": record["observed_through"]}
            if record.get("removed_at"):
                observation["first_absent_at"] = record["removed_at"]
            record["source_symbol_observations"] = [observation]
            record["source_symbols"] = [record["source_symbol"]]
            previous = coalesced[-1] if coalesced else None
            if (previous and previous["security_id"] == security_id
                    and metadata[security_id]["identity_status"] == "verified"
                    and previous.get("removed_at") == record["added_at"]):
                previous["observed_through"] = record["observed_through"]
                previous["source_symbol_observations"].extend(record["source_symbol_observations"])
                previous["source_symbols"] = sorted(set(previous["source_symbols"] + record["source_symbols"]))
                for key in ("removed_at", "removal_effective_date_known", "right_censored"):
                    previous.pop(key, None)
                    if key in record:
                        previous[key] = record[key]
            else:
                coalesced.append(record)
    membership = sorted(coalesced, key=lambda r: (r["source_symbol"], r["added_at"]))
    return {
        "metadata": list(metadata.values()), "membership": membership, "gics": [],
        "audit": {
            "source": source, "snapshot_count": len(snapshots),
            "first_snapshot": snapshots[0].observed_at,
            "last_snapshot": snapshots[-1].observed_at,
            "data_coverage_start": snapshots[0].observed_at,
            "data_coverage_end": snapshots[-1].observed_at,
            "unique_symbols": len(counts), "membership_intervals": len(membership),
            "source_membership_intervals": len(runs),
            "min_snapshot_size": min(len(s.symbols) for s in snapshots),
            "max_snapshot_size": max(len(s.symbols) for s in snapshots),
            "max_snapshot_gap_days": max((
                (date.fromisoformat(b.observed_at) - date.fromisoformat(a.observed_at)).days
                for a, b in zip(snapshots, snapshots[1:])
            ), default=0),
            "reentry_symbols": sorted(symbol for symbol, count in counts.items() if count > 1),
            "unresolved_identities": sum(m["identity_status"] == "unresolved" for m in metadata.values()),
            "warnings": ["Snapshot dates are observation bounds, not official effective dates.",
                         "Snapshots are not daily; changes between observations may be missing.",
                         "Source labels may be retrospectively normalized; historical ticker validity is unverified.",
                         "Noncontiguous symbol appearances remain separate until identity verification."],
        },
    }


def prepare(csv_path: str | Path, registry_path: str | Path | None = None,
            universe: str = "SP500") -> dict:
    """Public publisher interface; registry is a JSON list, never API credentials."""
    registry = json.loads(Path(registry_path).read_text()) if registry_path else None
    if registry is not None and not isinstance(registry, list):
        raise ValueError("Registry must be a JSON list")
    result = prepare_history(csv_path, registry, universe)
    return {"stock_metadata": result["metadata"],
            "stock_universe_membership": result["membership"],
            "stock_gics_history": result["gics"], "report": result["audit"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--universe", default="SP500")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = prepare(args.csv, args.registry, args.universe)
    args.output.write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan["report"], indent=2))
