import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verified_identities import build_registry


class VerifiedIdentityTests(unittest.TestCase):
    def test_google_classes_stay_distinct_and_original_ticker_is_dated(self):
        plan = {"stock_metadata": [
            {"security_id": "class-a", "source_symbol_observations": [{"symbol": "GOOGL", "observed_from": "2006-04-03"}]},
            {"security_id": "class-c", "source_symbol_observations": [{"symbol": "GOOG", "observed_from": "2014-04-03"}]}]}
        records = {r["symbol"]: r for r in build_registry(plan)}
        self.assertNotEqual(records["GOOG"]["security_id"], records["GOOGL"]["security_id"])
        self.assertEqual(records["GOOGL"]["ticker_aliases"][0]["symbol"], "GOOG")
        self.assertEqual(records["GOOGL"]["ticker_aliases"][0]["effective_to"], "2014-04-03")
        self.assertEqual(records["GOOG"]["ticker_aliases"][0]["effective_from"], "2014-04-03")
        self.assertIn("retrospectively", records["GOOGL"]["source_label_notes"])

    def test_meta_rename_preserves_fb_id_and_does_not_join_old_reused_symbol(self):
        plan = {"stock_metadata": [
            {"security_id": "meta-old", "source_symbol_observations": [{"symbol": "META", "observed_from": "1999-09-29"}]},
            {"security_id": "fb-first", "source_symbol_observations": [{"symbol": "FB", "observed_from": "2013-12-23"}]},
            {"security_id": "meta-new", "source_symbol_observations": [{"symbol": "META", "observed_from": "2022-06-09"}]}]}
        records = build_registry(plan)
        self.assertEqual(len(records), 2)
        self.assertEqual({r["security_id"] for r in records}, {"fb-first"})
        self.assertEqual(records[0]["ticker_aliases"][0]["effective_to"], "2022-06-09")


if __name__ == "__main__":
    unittest.main()
