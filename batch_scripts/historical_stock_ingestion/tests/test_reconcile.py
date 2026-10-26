import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile import reconcile


class ReconcileTests(unittest.TestCase):
    def plan(self, verified=False):
        return {"stock_metadata": [{"security_id": "keep-id", "identity_status": "verified" if verified else "unresolved",
                    "company_name": "Example Inc", "identity_evidence": ["issuer filing"] if verified else [],
                    "source_symbol_observations": [{"symbol": "EX", "observed_from": "2000-01-01", "observed_through": "2001-01-01"}]}],
                "stock_universe_membership": [], "stock_gics_history": [], "report": {}}

    def state(self, start=None):
        return {"research": {"EX": {"company_name": "Example Inc", "classifications": [{"taxonomy": "GICS", "sector": "Industrials",
                    "effective_from": start, "effective_to": None, "observed_at": "2026-10-26T01:00:00Z", "verification_status": "primary_source_verified", "source_urls": ["https://issuer.example/gics"]}],
                    "ticker_changes": [{"old_symbol": "OLD", "new_symbol": "EX", "effective_date": "2000-01-01", "source_urls": ["https://issuer.example/rename"]}]}}}

    def test_research_never_verifies_historical_identity(self):
        plan = self.plan()
        out = reconcile(plan, self.state("1999-01-01"))
        self.assertEqual(out["stock_gics_history"], [])
        self.assertEqual(out["stock_metadata"][0]["security_id"], "keep-id")
        self.assertEqual(len(out["stock_metadata"][0]["classification_candidates"]), 1)
        self.assertFalse(out["report"]["enrichment_review"]["registry_candidates"][0]["verified"])
        self.assertNotIn("classification_candidates", plan["stock_metadata"][0])

    def test_current_classification_retains_unknown_effective_date(self):
        out = reconcile(self.plan(True), self.state())
        row = out["stock_gics_history"][0]
        self.assertIsNone(row["effective_from"])
        self.assertEqual(row["date_basis"], "current_observation_only")
        self.assertTrue(row["classification_key"].startswith("observed#"))
        self.assertEqual(len(reconcile(out, self.state())["stock_gics_history"]), 1)
        later = self.state()
        later["research"]["EX"]["classifications"][0]["observed_at"] = "2026-11-26T01:00:00Z"
        again = reconcile(out, later)
        self.assertEqual(len(again["stock_gics_history"]), 1)
        self.assertEqual(again["stock_gics_history"][0]["observed_through"], "2026-11-26T01:00:00Z")
        later["research"]["EX"]["classifications"][0]["sector"] = "Energy"
        changed = reconcile(again, later)
        self.assertEqual(len(changed["stock_gics_history"]), 2)
        back = self.state()
        back["research"]["EX"]["classifications"][0]["observed_at"] = "2026-12-26T01:00:00Z"
        self.assertEqual(len(reconcile(changed, back)["stock_gics_history"]), 3)

    def test_historical_classification_requires_matching_verified_company(self):
        plan = self.plan(True)
        plan["stock_metadata"][0]["company_name"] = "Different issuer"
        self.assertEqual(reconcile(plan, self.state("1999-01-01"))["stock_gics_history"], [])
        out = reconcile(self.plan(True), self.state("1999-01-01"))
        self.assertTrue(out["stock_gics_history"][0]["classification_key"].startswith("effective#1999-01-01#"))

    def test_provider_taxonomy_not_promoted_to_gics(self):
        state = {"kg": {"EX": {"exact_listing_candidates": [{"name": "Example Inc", "sector": "Provider label"}]}}}
        out = reconcile(self.plan(), state)
        self.assertEqual(out["stock_gics_history"], [])
        self.assertEqual(out["stock_metadata"][0]["company_candidates"][0]["provider_classification"]["sector"], "Provider label")

    def test_sourced_identity_coverage_automatically_attaches_classification(self):
        state = self.state("1999-01-01")
        state["research"]["EX"]["observed_at"] = "2026-10-26T01:00:00Z"
        state["research"]["EX"]["identities"] = [{"company_name": "Example Inc",
            "historical_symbol_valid_from": "1990-01-01", "historical_symbol_valid_to": None,
            "ticker_reuse_checked": True, "source_urls": ["https://issuer.example/identity"]}]
        result = reconcile(self.plan(), state)
        self.assertEqual(len(result["stock_gics_history"]), 1)
        self.assertEqual(result["stock_metadata"][0]["identity_status"], "sourced_historical_identity")
        self.assertTrue(result["report"]["enrichment_review"]["verified_registry"][0]["verified"])
        state["research"]["EX"]["identities"][0]["historical_symbol_valid_from"] = "2000-06-01"
        self.assertEqual(reconcile(self.plan(), state)["stock_gics_history"], [])
        state["research"]["EX"]["identities"][0]["historical_symbol_valid_from"] = None
        self.assertEqual(reconcile(self.plan(), state)["stock_gics_history"], [])

    def test_verified_identity_does_not_verify_research_classification_dates(self):
        state = self.state("1999-01-01")
        state["research"]["EX"]["classifications"][0]["verification_status"] = "sourced_research_candidate"
        self.assertEqual(reconcile(self.plan(True), state)["stock_gics_history"], [])


if __name__ == "__main__":
    unittest.main()
