"""Collect classification facts from MSCI's public announcement search form.

The visible form's All date-range option provides a public dated URL list.
These notices omit sub-industry-only changes and are not complete GICS history.
No company/security identity joins are inferred here. Original text stays in memory.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_URL = "https://app2.msci.com/webapp/index_ann/"
LISTING_URL = BASE_URL + "Announcement"


def discover_notices(document: str) -> list[dict]:
    notices = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, re.I | re.S):
        link = re.search(r'<a\s+href="(DocGet\?[^\"]+)"[^>]*>(.*?)</a>', row, re.I | re.S)
        if not link or "GLOBAL INDUSTRY CLASSIFICATION STANDARD" not in html.unescape(link[2]).upper():
            continue
        date_match = re.search(r"\b(\d{2} [A-Z][a-z]{2} \d{4})\b", row)
        if not date_match:
            raise ValueError("GICS notice listing row has no publication date")
        notices.append({
            "announced_on": datetime.strptime(date_match[1], "%d %b %Y").date().isoformat(),
            "source_url": urllib.parse.urljoin(BASE_URL, html.unescape(link[1])),
            "requires_amendment_review": bool(re.search(r"AMEND|AMMEND|CORRECT", html.unescape(link[2]).upper())),
        })
    if not notices:
        raise ValueError("Public listing returned no GICS announcements")
    return list({notice["source_url"]: notice for notice in notices}.values())


def parse_notice(document: str, notice: dict) -> list[dict]:
    pre = re.search(r"<pre\b[^>]*>(.*?)</pre>", document, re.I | re.S)
    if not pre:
        raise ValueError("Public announcement has no content block")
    text = html.unescape(re.sub(r"<[^>]+>", "", pre[1])).replace("\xa0", " ")
    current_count = len(re.findall(r"CURRENT\s+GICS\s+SUB[- ]INDUSTRY", text, re.I))
    if not current_count:
        if re.search(r"No changes", text, re.I):
            return []
        raise ValueError("Unrecognized classification announcement layout")
    effective = re.search(r"as of the close of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
    if not effective:
        raise ValueError("Classification changes lack an explicit effective market-close date")
    effective_on = datetime.strptime(re.sub(r"\s+", " ", effective[1]).replace(",", ""), "%B %d %Y").date().isoformat()
    countries = set(re.findall(r"^([A-Z][A-Z &'-]+?)\s{2,}\d+\s*$", text, re.M))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    facts = []
    country = None
    for position, line in enumerate(lines):
        if line in countries:
            country = line
        if not re.match(r"CURRENT\s+GICS\s+SUB[- ]INDUSTRY", line, re.I):
            continue
        combined = " ".join(lines[position:position + 4])
        pair = re.match(r"CURRENT\s+GICS\s+SUB[- ]INDUSTRY\s+(\d{8})\s+(.+?)\s+NEW\s+GICS\s+SUB[- ]INDUSTRY\s+(\d{8})\s+(.+?)(?=\s+(?:CURRENT|NEW)\s+GICS|$)", combined, re.I)
        # Names are taken separately from the label lines so following companies
        # and country headings can never leak into a classification name.
        old = re.search(r"CURRENT\s+GICS\s+SUB[- ]INDUSTRY\s+(\d{8})\s+([^\n]+)", "\n".join(lines[position:]), re.I)
        new = re.search(r"NEW\s+GICS\s+SUB[- ]INDUSTRY\s+(\d{8})\s+([^\n]+)", "\n".join(lines[position:]), re.I)
        if not old or not new or position == 0 or not pair:
            raise ValueError("Unrecognized old/new GICS classification pair")
        facts.append({
            "company_name": lines[position - 1], "country": country,
            "old_gics_sub_industry_code": old[1], "old_gics_sub_industry_name": old[2].strip(),
            "new_gics_sub_industry_code": new[1], "new_gics_sub_industry_name": new[2].strip(),
            "effective_on": effective_on, "effective_timing": "market_close",
            "announced_on": notice["announced_on"], "source_url": notice["source_url"],
            "requires_amendment_review": notice["requires_amendment_review"],
            "source": "MSCI public GICS announcement",
        })
    if len(facts) != current_count:
        raise ValueError("Incomplete classification extraction")
    return facts


def reconcile_corrections(result: dict) -> None:
    """Flag date-override notices and the original facts they may invalidate."""
    corrections = []
    for error in result["errors"]:
        document = fetch(error["source_url"])
        pre = re.search(r"<pre\b[^>]*>(.*?)</pre>", document, re.I | re.S)
        if not pre:
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", pre[1]))
        if not re.search(r"(?:update|correction|contrary).*(?:GICS|announcement)", text, re.I):
            continue
        reference = re.search(r"(?:announcement sent|refer to the)\s+(?:last\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
        effective = re.search(r"as of the close of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
        if reference and effective:
            parse = lambda value: datetime.strptime(re.sub(r"\s+", " ", value).replace(",", ""), "%B %d %Y").date().isoformat()
            corrections.append({"affected_announced_on": parse(reference[1]),
                                "corrected_effective_on": parse(effective[1]),
                                "effective_timing": "market_close", "source_url": error["source_url"],
                                "announced_on": error["announced_on"], "requires_amendment_review": True})
    result["date_corrections"] = corrections
    for fact in result["classification_changes"]:
        related = [correction for correction in corrections
                   if correction["affected_announced_on"] == fact["announced_on"]]
        if related:
            fact["requires_amendment_review"] = True
            fact["possible_date_correction_sources"] = [item["source_url"] for item in related]


def fetch(url: str, data: bytes | None = None) -> str:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "JimmyClassificationIngestion/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def collect(output: Path, *, limit: int | None = None, listing_html: Path | None = None) -> dict:
    if listing_html:
        listing = listing_html.read_text()
    else:
        # These are the exact visible public form field names and option values.
        fields = {"doc_type": "ANNOUNCEMENT", "format": "html", "lang": "en",
                  "prod_type": "STANDARD", "visibility": "public", "date_range": "0",
                  "submit": "View Announcements"}
        listing = fetch(LISTING_URL, urllib.parse.urlencode(fields).encode())
    notices = discover_notices(listing)
    result = {"source": "MSCI public standard announcements", "complete_gics_history": False,
              "limitations": ["Sub-industry-only changes are not announced", "Corporate-event and structural-review changes may require separate sources", "Company names are not automatically joined to security IDs", "Effective dates represent market close"],
              "discovered_notices": len(notices), "classification_changes": [], "errors": [], "processed_notices": 0}
    for notice in notices[:limit]:
        try:
            facts = parse_notice(fetch(notice["source_url"]), notice)
            result["classification_changes"].extend(facts)
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            result["errors"].append({**notice, "error": str(error)})
        result["processed_notices"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        time.sleep(0.1)
    reconcile_corrections(result)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--listing-html", type=Path)
    args = parser.parse_args()
    result = collect(args.output, limit=args.limit, listing_html=args.listing_html)
    print(json.dumps({"discovered": result["discovered_notices"], "processed": result["processed_notices"],
                      "changes": len(result["classification_changes"]), "errors": len(result["errors"])}))


if __name__ == "__main__":
    main()
