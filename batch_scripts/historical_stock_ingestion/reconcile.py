"""Merge classification/identity facts while retaining explicit verification gates."""
from __future__ import annotations
import argparse
import hashlib
from copy import deepcopy
from datetime import date
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def valid_urls(values):
    return sorted({v for v in values or [] if isinstance(v, str)
                   and urlsplit(v).scheme in {"https", "http"} and urlsplit(v).hostname})


def valid_date(value):
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


GICS_STATE_FIELDS = ("sector_code", "sector", "industry_group_code", "industry_group",
                     "industry_code", "industry", "sub_industry_code", "sub_industry")


def same_state(a, b):
    common = [k for k in GICS_STATE_FIELDS if a.get(k) is not None and b.get(k) is not None]
    return bool(common) and all(a[k] == b[k] for k in common)


def state_fingerprint(row):
    fields = {k: row.get(k) for k in GICS_STATE_FIELDS}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def corroborated_identity(research, observations):
    """Require sourced identity intervals to cover every observed source label.

    Open-ended identity evidence covers only through the research observation
    date. Unknown starts cannot verify a historical run. Multiple competing
    companies covering a run are ambiguous even when one resembles KG data.
    """
    company = research.get("company_name")
    if not company or not observations:
        return None
    matches = []
    for observed in observations:
        candidates = []
        for identity in research.get("identities", []):
            urls = valid_urls(identity.get("source_urls"))
            start = identity.get("historical_symbol_valid_from")
            end = identity.get("historical_symbol_valid_to")
            try:
                if not urls or identity.get("ticker_reuse_checked") is not True or not valid_date(start):
                    continue
                if end and (not valid_date(end) or end <= start):
                    continue
            except ValueError:
                continue
            as_of = research.get("observed_at", "")[:10]
            if start <= observed["observed_from"] and (observed["observed_through"] < end if end else observed["observed_through"] <= as_of):
                candidates.append(identity)
        names = {normalized_name(c.get("company_name")) for c in candidates}
        if names != {normalized_name(company)}:
            return None
        matches.extend(candidates)
    return {"company_name": company,
            "source_urls": sorted({url for identity in matches for url in valid_urls(identity.get("source_urls"))}),
            "verification_status": "sourced_historical_identity", "ticker_reuse_checked": True}


def merge_supplement(enrichment, supplement):
    combined = deepcopy(enrichment)
    for symbol, primary in supplement.get("research", {}).items():
        original = combined.setdefault("research", {}).get(symbol, {})
        merged = {**original, **deepcopy(primary)}
        merged["classifications"] = deepcopy(primary.get("classifications", []))
        merged["classifications"].extend(c for c in original.get("classifications", [])
                                         if c not in merged["classifications"])
        combined["research"][symbol] = merged
    return combined


def share_verified_issuer_classifications(plan, enrichment):
    """Verified share classes retain identities while sharing issuer-level GICS."""
    combined = deepcopy(enrichment)
    researched = combined.setdefault("research", {})
    for meta in plan["stock_metadata"]:
        if meta.get("identity_status") != "verified" or not meta.get("share_class"):
            continue
        for observation in meta["source_symbol_observations"]:
            symbol = observation["symbol"]
            if researched.get(symbol, {}).get("classifications"):
                continue
            candidates = [r for r in researched.values()
                          if normalized_name(r.get("company_name")) == normalized_name(meta.get("company_name"))
                          and r.get("classifications")]
            if candidates:
                researched[symbol] = {"symbol": symbol, "company_name": meta["company_name"],
                    "classifications": deepcopy(candidates[0]["classifications"]),
                    "identities": [], "ticker_changes": [],
                    "observed_at": candidates[0].get("observed_at"),
                    "classification_scope": "issuer_shared_across_verified_share_classes"}
    return combined


def reconcile(plan: dict, enrichment: dict, taxonomy: dict | None = None) -> dict:
    """Only an evidenced identity with a research-company match can publish GICS.

    Metadata ``company_name`` and ``identity_evidence`` must be supplied by an
    explicit verified registry, or sourced ownership intervals covering the run.
    Symbol equality and current listing equality do not verify a historical
    security. Every other result stays under candidate fields/review, preserving
    facts without rewriting IDs. URL references are retained evidence supplied
    by the provider; this pure module does not fetch their page contents.
    """
    out = deepcopy(plan)
    review = {"company_candidates": 0, "classification_candidates": 0,
              "published_classifications": 0, "ticker_change_candidates": [],
              "registry_candidates": [], "verified_registry": [], "identity_unresolved": [], "rejected": []}
    memberships = {}
    for row in out["stock_universe_membership"]:
        memberships.setdefault(row["security_id"], []).append(row)
    existing_keys = {(r["security_id"], r["classification_key"]) for r in out["stock_gics_history"]}
    for meta in out["stock_metadata"]:
        sid = meta["security_id"]
        observations = meta.get("source_symbol_observations", [])
        symbols = sorted({r["symbol"] for r in observations})
        companies, classifications = [], []
        for symbol in symbols:
            kg = enrichment.get("kg", {}).get(symbol, {})
            candidates = kg.get("exact_listing_candidates", [])
            for candidate in candidates:
                company = {k: candidate[k] for k in ("id", "name", "country", "listing_values") if k in candidate}
                company.update({"source_symbol": symbol, "observed_at": kg.get("observed_at"),
                                "verification_status": "current_listing_candidate",
                                "historical_identity_verified": False})
                company["provider_classification"] = {k: candidate[k] for k in ("sector", "industry_group", "industry") if k in candidate}
                company["provider_taxonomy"] = kg.get("provider_taxonomy", "Bigdata provider classification; not GICS")
                if company not in companies:
                    companies.append(company)
            research = enrichment.get("research", {}).get(symbol, {})
            company_name = research.get("company_name")
            identity_match = corroborated_identity(research, [r for r in observations if r["symbol"] == symbol])
            if identity_match:
                evidence = meta.setdefault("historical_identity_evidence", [])
                if identity_match not in evidence:
                    evidence.append(identity_match)
                meta["company_name"] = company_name
                if meta.get("identity_status") != "verified":
                    meta["identity_status"] = "sourced_historical_identity"
                for observed in observations:
                    if observed["symbol"] == symbol:
                        review["verified_registry"].append({"symbol": symbol,
                            "observed_from": observed["observed_from"], "security_id": sid,
                            "verified": True, "company_name": company_name,
                            "evidence": "Sourced historical identity coverage: " + " ".join(identity_match["source_urls"])})
            if company_name:
                companies.append({"name": company_name, "source_symbol": symbol,
                                  "verification_status": "sourced_research_candidate",
                                  "observed_at": research.get("observed_at"),
                                  "historical_identity_verified": False})
            for classification in research.get("classifications", []):
                urls = valid_urls(classification.get("source_urls"))
                if classification.get("taxonomy") != "GICS" or not urls:
                    review["rejected"].append({"security_id": sid, "symbol": symbol, "reason": "GICS taxonomy and source URLs required"})
                    continue
                candidate = {k: classification[k] for k in (
                    "taxonomy", "sector_code", "sector", "industry_group_code", "industry_group",
                    "industry_code", "industry", "sub_industry_code", "sub_industry",
                    "effective_from", "effective_to", "observed_at", "date_basis", "verification_status",
                    "source_effective_at", "effective_date_convention", "taxonomy_source_url") if k in classification}
                candidate.update(source_symbol=symbol, company_name=company_name, source_urls=urls)
                classifications.append(candidate)
                # A registry must explicitly confirm company identity across the
                # membership observations before classification can be joined.
                verified = bool(identity_match) or (
                    meta.get("identity_status") == "verified" and bool(meta.get("identity_evidence"))
                    and bool(company_name) and normalized_name(meta.get("company_name")) == normalized_name(company_name))
                if not verified:
                    continue
                start, end = candidate.get("effective_from"), candidate.get("effective_to")
                if (start or end) and candidate.get("verification_status") != "primary_source_verified":
                    review["rejected"].append({"security_id": sid, "symbol": symbol,
                        "reason": "Historical classification dates require independently verified source facts; identity verification is insufficient"})
                    continue
                if taxonomy:
                    codes = taxonomy["codes"]
                    code = candidate.get("sub_industry_code")
                    if code and code in codes:
                        for size, code_key, label_key in ((2, "sector_code", "sector"),
                                (4, "industry_group_code", "industry_group"),
                                (6, "industry_code", "industry"), (8, "sub_industry_code", "sub_industry")):
                            parent = code[:size]
                            candidate[code_key] = parent
                            candidate[label_key] = codes[parent]
                        candidate["taxonomy_source_url"] = taxonomy["source_url"]
                    elif candidate.get("verification_status") != "primary_source_verified":
                        review["rejected"].append({"security_id": sid, "symbol": symbol,
                            "reason": "Research classification must map to canonical GICS subindustry code"})
                        continue
                try:
                    if start and (not valid_date(start) or (end and (not valid_date(end) or end <= start))):
                        continue
                    if start and start < "1999-01-01":
                        review["rejected"].append({"security_id": sid, "symbol": symbol,
                            "reason": "GICS effective date predates taxonomy inception"})
                        continue
                    if not start and not candidate.get("observed_at"):
                        continue
                    if not start and end and (not valid_date(end) or end <= candidate["observed_at"][:10]):
                        continue
                except ValueError:
                    continue
                basis = "confirmed_effective_date" if start else candidate.get("date_basis", "current_observation_only")
                earliest_alias = min((a["effective_from"] for a in meta.get("ticker_aliases", [])), default=None)
                if start and earliest_alias and start < earliest_alias:
                    review["rejected"].append({"security_id": sid, "symbol": symbol,
                        "reason": "Classification effective date predates verified security listing"})
                    continue
                row = {**candidate, "security_id": sid, "date_basis": basis,
                       "classification_key": ("effective#" + start if start else "observed#" + candidate["observed_at"]) + "#" + state_fingerprint(candidate),
                       "verification_status": "sourced_classification_verified_identity"}
                key = (sid, row["classification_key"])
                if not start:
                    previous = [r for r in out["stock_gics_history"] if r["security_id"] == sid
                                and (not r.get("effective_from") or r["effective_from"] <= candidate["observed_at"][:10])
                                and (not r.get("effective_to") or candidate["observed_at"][:10] < r["effective_to"])]
                    latest = max(previous, key=lambda r: r.get("effective_from") or r.get("observed_at", ""), default=None)
                    if latest and same_state(latest, row):
                        latest["observed_through"] = max(latest.get("observed_through", latest["observed_at"]), row["observed_at"])
                        latest["source_urls"] = sorted(set(latest.get("source_urls", []) + urls))
                        continue
                if key not in existing_keys:
                    existing_keys.add(key)
                    out["stock_gics_history"].append(row)
                    review["published_classifications"] += 1
            for change in research.get("ticker_changes", []):
                urls = valid_urls(change.get("source_urls"))
                try:
                    if not urls or not valid_date(change.get("effective_date")):
                        continue
                except ValueError:
                    continue
                fact = {k: change[k] for k in ("old_symbol", "new_symbol", "effective_date")}
                fact.update(source_urls=urls, verification_status="sourced_research_candidate")
                if fact not in review["ticker_change_candidates"]:
                    review["ticker_change_candidates"].append(fact)
                for observed in observations:
                    review["registry_candidates"].append({"symbol": observed["symbol"],
                        "observed_from": observed["observed_from"], "security_id": sid,
                        "verified": False, "ticker_change_candidate": fact,
                        "required_review": "Corroborate issuer/security identity, ticker dates and source run coverage; preserve assigned security ID."})
        meta["company_candidates"] = companies
        meta["classification_candidates"] = classifications
        review["company_candidates"] += len(companies)
        review["classification_candidates"] += len(classifications)
        if meta.get("identity_status") not in {"verified", "sourced_historical_identity"}:
            review["identity_unresolved"].append(sid)
    out["report"]["enrichment_review"] = review
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--enrichment", required=True, type=Path)
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--taxonomy", type=Path,
                        default=Path(__file__).parent / "artifacts" / "gics_dictionary.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    enrichment = json.loads(args.enrichment.read_text())
    if args.supplement:
        enrichment = merge_supplement(enrichment, json.loads(args.supplement.read_text()))
    enrichment = share_verified_issuer_classifications(plan, enrichment)
    taxonomy = json.loads(args.taxonomy.read_text()) if args.taxonomy.exists() else None
    result = reconcile(plan, enrichment, taxonomy)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    report = result["report"]["enrichment_review"]
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in report.items()}, indent=2))


if __name__ == "__main__":
    main()
