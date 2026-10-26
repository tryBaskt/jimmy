from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from current_snapshot import attach_current_snapshot


class CurrentSnapshotTests(unittest.TestCase):
    def fixtures(self):
        taxonomy = {"source_url": "https://taxonomy.example/gics", "codes": {
            "45": "Information Technology", "4510": "Software & Services",
            "451030": "Software", "45103020": "Systems Software"}}
        classification = {"taxonomy": "GICS", "sector_code": "45", "sector": "Information Technology",
            "industry_group_code": "4510", "industry_group": "Software & Services",
            "industry_code": "451030", "industry": "Software", "sub_industry_code": "45103020",
            "sub_industry": "Systems Software", "effective_from": None, "effective_to": None,
            "observed_at": "2026-10-26T01:00:00Z", "source_urls": ["https://source.example/components"],
            "verification_status": "deterministic_source_extraction"}
        metadata = {"security_id": "keep-id", "identity_status": "unresolved", "source_symbol_observations": [
            {"symbol": "EX", "observed_from": "1996-01-02", "observed_through": "2026-08-18"}]}
        plan = {"stock_metadata": [metadata], "stock_universe_membership": [], "stock_gics_history": [],
                "report": {"data_coverage_end": "2026-08-18"}}
        snapshot = {"research": {"EX": {"company_name": "ExampleClassA", "source_security_name": "ExampleClassA",
            "cik": "0000000001", "observed_at": classification["observed_at"],
            "source_urls": classification["source_urls"], "classifications": [classification]}}}
        return plan, snapshot, taxonomy

    def test_current_identity_does_not_verify_history_and_resumes_idempotently(self):
        plan, snapshot, taxonomy = self.fixtures()
        out = attach_current_snapshot(plan, snapshot, taxonomy)
        meta = out["stock_metadata"][0]
        self.assertEqual(meta["security_id"], "keep-id")
        self.assertEqual(meta["identity_status"], "sourced_current_identity")
        self.assertFalse(meta["historical_identity_verified"])
        row = out["stock_gics_history"][0]
        self.assertIsNone(row["effective_from"])
        self.assertEqual(row["date_basis"], "current_observation_only")
        again = attach_current_snapshot(out, snapshot, taxonomy)
        self.assertEqual(len(again["stock_gics_history"]), 1)

    def test_no_ambiguous_or_retired_run_matching(self):
        plan, snapshot, taxonomy = self.fixtures()
        plan["stock_metadata"].append(deepcopy(plan["stock_metadata"][0]))
        plan["stock_metadata"][-1]["security_id"] = "second-id"
        out = attach_current_snapshot(plan, snapshot, taxonomy)
        self.assertEqual(out["stock_gics_history"], [])
        self.assertEqual(out["report"]["current_snapshot_review"]["ambiguous_symbols"], ["EX"])
        plan["stock_metadata"] = plan["stock_metadata"][:1]
        plan["stock_metadata"][0]["source_symbol_observations"][0]["observed_through"] = "2000-01-01"
        self.assertEqual(attach_current_snapshot(plan, snapshot, taxonomy)["stock_gics_history"], [])

    def test_secondary_snapshot_cannot_override_corrobated_primary_change(self):
        plan, snapshot, taxonomy = self.fixtures()
        prior = deepcopy(snapshot["research"]["EX"]["classifications"][0])
        prior.update({"security_id": "keep-id", "classification_key": "effective#2026-10-01#prior",
                      "effective_from": "2026-10-01", "date_basis": "confirmed_effective_date", "sector": "Different"})
        plan["stock_gics_history"] = [prior]
        out = attach_current_snapshot(plan, snapshot, taxonomy)
        self.assertEqual(len(out["stock_gics_history"]), 1)
        self.assertEqual(len(out["report"]["current_snapshot_review"]["conflicting_primary_classifications"]), 1)


if __name__ == "__main__":
    unittest.main()
