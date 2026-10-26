import csv
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snapshot_history import prepare, prepare_history, read_snapshots


class SnapshotHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "snapshots.csv"

    def write(self, rows):
        with self.path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["date", "tickers"])
            writer.writerows(rows)

    def test_observation_bounds_censoring_and_reentry(self):
        self.write([("2000-01-01", "ABC,KEEP"), ("2000-01-10", "KEEP"),
                    ("2000-02-01", "ABC,KEEP")])
        result = prepare(self.path)
        records = result["stock_universe_membership"]
        abc = [r for r in records if r["source_symbol"] == "ABC"]
        self.assertEqual(abc[0]["removed_at"], "2000-01-10")
        self.assertEqual(abc[0]["observed_through"], "2000-01-01")
        self.assertTrue(abc[0]["left_censored"])
        self.assertNotEqual(abc[0]["security_id"], abc[1]["security_id"])
        self.assertEqual(abc[1]["previous_absent_at"], "2000-01-10")
        self.assertNotIn("removed_at", abc[1])
        self.assertTrue(abc[1]["right_censored"])
        self.assertEqual(abc[0]["dataset_observed_through"], "2000-02-01")
        self.assertEqual(result["stock_gics_history"], [])

    def test_verified_rename_links_identity_without_inventing_ticker_dates(self):
        self.write([("2000-01-01", "OLD"), ("2000-02-01", "NEW")])
        registry = [{"symbol": s, "observed_from": d, "security_id": "sec-1",
                     "verified": True, "evidence": "issuer filing"}
                    for s, d in [("OLD", "2000-01-01"), ("NEW", "2000-02-01")]]
        result = prepare_history(self.path, registry)
        self.assertEqual(len(result["metadata"]), 1)
        self.assertEqual(result["metadata"][0]["ticker_aliases"], [])
        self.assertEqual({r["security_id"] for r in result["membership"]}, {"sec-1"})
        self.assertEqual(len(result["membership"]), 1)
        self.assertEqual(result["membership"][0]["source_symbol"], "OLD")
        self.assertEqual(result["membership"][0]["source_symbols"], ["NEW", "OLD"])
        self.assertNotIn("removed_at", result["membership"][0])

    def test_unverified_or_overlapping_identity_is_rejected(self):
        self.write([("2000-01-01", "A,B")])
        registry = [{"symbol": s, "observed_from": "2000-01-01", "security_id": "same",
                     "verified": True, "evidence": "filing"} for s in ["A", "B"]]
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            prepare_history(self.path, registry)
        registry[0]["verified"] = False
        with self.assertRaisesRegex(ValueError, "verified evidence"):
            prepare_history(self.path, registry[:1])

    def test_append_preserves_existing_ids(self):
        self.write([("2000-01-01", "A")])
        first = prepare_history(self.path)["metadata"][0]["security_id"]
        self.write([("2000-01-01", "A"), ("2000-02-01", "A,B")])
        records = prepare_history(self.path)["metadata"]
        a = next(r for r in records if r["source_symbol_observations"][0]["symbol"] == "A")
        self.assertEqual(a["security_id"], first)

    def test_invalid_snapshots_fail_instead_of_silent_repair(self):
        for rows in [[("2000-01-01", "A,A")], [("2000-01-01", "A,")],
                     [("2000-01-01", "a")], [("2000-01-02", "A"), ("2000-01-01", "A")],
                     [("2000-01-01", "A"), ("2000-01-01", "B")]]:
            with self.subTest(rows=rows):
                self.write(rows)
                with self.assertRaises(ValueError):
                    read_snapshots(self.path)


if __name__ == "__main__":
    unittest.main()
