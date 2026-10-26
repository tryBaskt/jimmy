import pytest

from batch_scripts.historical_stock_ingestion.msci_public import discover_notices, parse_notice


def test_public_listing_discovers_only_dated_gics_links():
    listing = '''<table><tr><td>01 Sep 2026</td><td><a href="DocGet?pub_key=abc%3D&amp;lang=en">GLOBAL INDUSTRY CLASSIFICATION STANDARD (GICS) CHANGES</a></td></tr>
    <tr><td>01 Sep 2026</td><td><a href="DocGet?pub_key=other">OTHER NOTICE</a></td></tr></table>'''
    notices = discover_notices(listing)
    assert len(notices) == 1
    assert notices[0]["announced_on"] == "2026-09-01"
    assert notices[0]["source_url"].endswith("pub_key=abc%3D&lang=en")


def test_classification_facts_preserve_market_close_and_each_company():
    document = '''<pre>The following Industry changes will take place as of the close of
    September 30, 2026.
USA                      2

USA

F5
CURRENT GICS SUB-INDUSTRY 45201020 Communications Equipment
NEW GICS SUB-INDUSTRY 45103020 Systems Software

OTHER COMPANY
CURRENT GICS SUB-INDUSTRY 45103010 Application Software
NEW GICS SUB-INDUSTRY 50201010 Advertising

* Twice a month...</pre>'''
    facts = parse_notice(document, {"announced_on": "2026-09-01", "source_url": "https://example.test/notice", "requires_amendment_review": False})
    assert len(facts) == 2
    assert facts[0]["company_name"] == "F5"
    assert facts[0]["new_gics_sub_industry_name"] == "Systems Software"
    assert facts[0]["country"] == "USA"
    assert facts[0]["effective_on"] == "2026-09-30"
    assert facts[0]["effective_timing"] == "market_close"
    assert facts[1]["company_name"] == "OTHER COMPANY"
    assert "effective_from" not in facts[0]


def test_missing_effective_date_is_not_invented():
    with pytest.raises(ValueError, match="explicit effective"):
        parse_notice('<pre>F5\nCURRENT GICS SUB-INDUSTRY 45201020 Communications Equipment\nNEW GICS SUB-INDUSTRY 45103020 Systems Software</pre>', {})
