"""Build reviewed source-run mappings from independently checked issuer facts.

These facts identify securities, including share class, rather than joining
names or ticker strings. Source snapshot labels remain distinct from aliases.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

META_RENAME = "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm"
META_LISTING = "https://investor.atmeta.com/files/doc_financials/annual_reports/FB2014AR.pdf"
GOOGLE_LISTING = "https://abc.xyz/assets/investor/static/pdf/2014_google_annual_report.pdf"
APPLE_IDENTITY = "https://investor.apple.com/faq./default.aspx"
F5_IDENTITY = "https://www.sec.gov/Archives/edgar/data/1048695/000089102003002548/v93946e10vk.htm"


def alias(symbol, start, end, evidence):
    record = {"symbol": symbol, "effective_from": start, "evidence": evidence,
              "source_urls": [evidence], "date_basis": "confirmed_effective_date"}
    if end:
        record["effective_to"] = end
    return record


def build_registry(plan):
    by_symbol = {}
    for metadata in plan["stock_metadata"]:
        for observation in metadata["source_symbol_observations"]:
            by_symbol.setdefault(observation["symbol"], []).append((metadata, observation))
    registry = []
    meta_id = by_symbol.get("FB", by_symbol.get("META", [(None, None)]))[0][0]
    for symbol, records in by_symbol.items():
        for metadata, observation in records:
            sid, name, proof, aliases, share_class = metadata["security_id"], None, None, [], None
            if symbol == "AAPL":
                name, proof = "Apple Inc.", APPLE_IDENTITY
                aliases = [alias("AAPL", "1980-12-12", None, proof)]
            elif symbol == "FFIV" and observation["observed_from"] >= "1999-06-04":
                name, proof = "F5, Inc.", F5_IDENTITY
                aliases = [alias("FFIV", "1999-06-04", None, proof)]
            elif symbol in {"FB", "META"} and observation["observed_from"] >= "2012-05-18":
                name, proof, share_class = "Meta Platforms, Inc.", META_RENAME, "Class A"
                sid = meta_id["security_id"]
                aliases = [alias("FB", "2012-05-18", "2022-06-09", META_LISTING),
                           alias("META", "2022-06-09", None, META_RENAME)]
            elif symbol == "GOOGL" and observation["observed_from"] >= "2004-08-19":
                name, proof, share_class = "Alphabet Inc.", GOOGLE_LISTING, "Class A"
                aliases = [alias("GOOG", "2004-08-19", "2014-04-03", proof),
                           alias("GOOGL", "2014-04-03", None, proof)]
            elif symbol == "GOOG" and observation["observed_from"] >= "2014-04-03":
                name, proof, share_class = "Alphabet Inc.", GOOGLE_LISTING, "Class C"
                aliases = [alias("GOOG", "2014-04-03", None, proof)]
            if not name:
                continue
            record = {"symbol": symbol, "observed_from": observation["observed_from"],
                      "security_id": sid, "company_name": name, "verified": True,
                      "evidence": proof, "ticker_aliases": aliases}
            if share_class:
                record["share_class"] = share_class
            if symbol == "GOOGL" and observation["observed_from"] < "2014-04-03":
                record["source_label_notes"] = "CSV retrospectively labels Class A as GOOGL before 2014; actual historical ticker was GOOG."
            registry.append(record)
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry = build_registry(json.loads(args.plan.read_text()))
    args.output.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"Prepared {len(registry)} verified source-run mappings")


if __name__ == "__main__":
    main()
