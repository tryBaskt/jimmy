"""Attach deterministic current classifications to unambiguous latest source runs.

This corroborates current identity only. Historical aliases, memberships and
identity evidence retain their original dates and verification status.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
from reconcile import GICS_STATE_FIELDS, same_state, state_fingerprint, valid_urls


def attach_current_snapshot(plan, snapshot, taxonomy):
    result = deepcopy(plan)
    metadata = {r["security_id"]: r for r in result["stock_metadata"]}
    final_date = result["report"]["data_coverage_end"]
    index = {}
    for row in metadata.values():
        for observation in row["source_symbol_observations"]:
            if observation["observed_through"] == final_date:
                index.setdefault(observation["symbol"], []).append(row)
    review = {"source_symbols": len(snapshot["research"]), "matched": 0,
              "inserted_classifications": 0, "extended_classifications": 0,
              "unmatched_symbols": [], "ambiguous_symbols": [], "invalid_classifications": [],
              "conflicting_primary_classifications": []}
    for symbol, source in sorted(snapshot["research"].items()):
        matches = index.get(symbol, [])
        if len(matches) != 1:
            review["unmatched_symbols" if not matches else "ambiguous_symbols"].append(symbol)
            continue
        meta = matches[0]
        rows = source.get("classifications", [])
        if len(rows) != 1:
            review["invalid_classifications"].append(symbol)
            continue
        classification = deepcopy(rows[0])
        code = classification.get("sub_industry_code")
        codes = taxonomy["codes"]
        if (classification.get("taxonomy") != "GICS" or code not in codes
                or classification.get("effective_from") or classification.get("effective_to")
                or not valid_urls(classification.get("source_urls"))
                or not classification.get("observed_at")
                or classification.get("verification_status") != "deterministic_source_extraction"):
            review["invalid_classifications"].append(symbol)
            continue
        canonical = {}
        for length, code_key, label_key in ((2, "sector_code", "sector"), (4, "industry_group_code", "industry_group"),
                                            (6, "industry_code", "industry"), (8, "sub_industry_code", "sub_industry")):
            canonical[code_key], canonical[label_key] = code[:length], codes[code[:length]]
        if any(classification.get(k) != value for k, value in canonical.items()):
            review["invalid_classifications"].append(symbol)
            continue
        review["matched"] += 1
        meta["company_name"] = source["company_name"]
        meta["source_security_name"] = source["source_security_name"]
        meta["cik"] = source["cik"]
        meta["symbol"] = symbol
        meta["symbol_basis"] = "current_listing_source"
        meta["current_identity_observed_at"] = source["observed_at"]
        meta["current_identity_source_urls"] = valid_urls(source.get("source_urls"))
        if meta["identity_status"] not in {"verified", "sourced_historical_identity"}:
            meta["identity_status"] = "sourced_current_identity"
            meta["historical_identity_verified"] = False
        sid, observed_at = meta["security_id"], classification["observed_at"]
        prior = [r for r in result["stock_gics_history"] if r["security_id"] == sid
                 and (not r.get("effective_from") or r["effective_from"] <= observed_at[:10])
                 and (not r.get("effective_to") or observed_at[:10] < r["effective_to"])]
        latest = max(prior, key=lambda r: r.get("effective_from") or r.get("observed_at", ""), default=None)
        if latest and same_state(latest, classification):
            latest["observed_through"] = max(latest.get("observed_through", latest.get("observed_at", "")), observed_at)
            latest["source_urls"] = sorted(set(latest.get("source_urls", []) + classification["source_urls"]))
            review["extended_classifications"] += 1
            continue
        if latest and latest.get("date_basis") == "confirmed_effective_date":
            review["conflicting_primary_classifications"].append({"symbol": symbol,
                "security_id": sid, "primary_classification_key": latest["classification_key"],
                "current_source_sub_industry_code": classification["sub_industry_code"],
                "reason": "Current secondary-source snapshot conflicts with a corroborated primary-source change; retained primary classification."})
            meta.setdefault("classification_candidates", []).append(classification)
            continue
        classification.update({"security_id": sid, "source_symbol": symbol,
            "date_basis": "current_observation_only",
            "classification_key": "observed#" + observed_at + "#" + state_fingerprint(classification)})
        result["stock_gics_history"].append(classification)
        review["inserted_classifications"] += 1
    result["report"]["current_snapshot_review"] = review
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--taxonomy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = attach_current_snapshot(json.loads(args.plan.read_text()), json.loads(args.snapshot.read_text()),
                                   json.loads(args.taxonomy.read_text()))
    args.output.write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan["report"]["current_snapshot_review"], indent=2))


if __name__ == "__main__":
    main()
