"""Offline tests for the Zillow Research housing snapshot (no network)."""
import unittest
from datetime import date

from scraper.housing import heat_label, parse_county_csv, summarize

CSV = (
    "RegionID,SizeRank,RegionName,RegionType,StateName,State,Metro,StateCodeFIPS,MunicipalCodeFIPS,"
    "2025-08-31,2025-09-30,2026-08-31,2026-09-30\n"
    "1,10,Greene County,county,GA,GA,,13,133,400000,402000,,442200.4\n"
    "2,11,Bibb County,county,GA,GA,\"Macon-Bibb County, GA\",13,021,160000,,168000,170000\n"
    "3,12,Greene County,county,AL,AL,,1,63,90000,91000,92000,93000\n"
    "4,13,Hancock County,county,GA,GA,,13,141,,,,\n"
)


class Housing(unittest.TestCase):
    def test_matches_on_fips_not_name(self):
        got = parse_county_csv(CSV, {"13133", "13021", "13141"})
        self.assertEqual(set(got), {"13133", "13021"})      # AL Greene excluded, empty row dropped
        self.assertEqual(got["13133"][-1], (date(2026, 9, 30), 442200.4))
        self.assertEqual(len(got["13133"]), 3)               # blank month skipped

    def test_summary_changes(self):
        got = parse_county_csv(CSV, {"13133", "13021"})
        greene = summarize(got["13133"], "usd")
        self.assertEqual(greene["value"], 442200)
        self.assertEqual(greene["as_of"], "2026-09")
        self.assertEqual(greene["yoy_pct"], 10.0)
        self.assertIsNone(greene["mom_pct"])                 # August 2026 is blank
        bibb = summarize(got["13021"], "usd")
        self.assertEqual(bibb["mom_pct"], 1.2)
        self.assertIsNone(bibb["yoy_pct"])                   # September 2025 is blank

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            parse_county_csv("a,b\n1,2\n", {"13133"})

    def test_heat_bands(self):
        self.assertEqual(heat_label(72), "strong seller's market")
        self.assertEqual(heat_label(50), "neutral")
        self.assertEqual(heat_label(20), "strong buyer's market")


if __name__ == "__main__":
    unittest.main()
