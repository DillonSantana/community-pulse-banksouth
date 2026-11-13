"""Offline tests for the hub calendar export."""
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from scraper.dates import find_time
from scraper.hub_export import build_events, is_hub_event, write_js
from scraper.models import Item

TODAY = date(2026, 11, 13)


def _it(title, d, **kw):
    base = dict(county="Morgan", region="R", kind="agenda", title=title, url="https://x/" + title,
                source="civicplus.agendacenter", event_date=d)
    base.update(kw)
    return Item(**base)


class Times(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(find_time("05:00 PM - 11:59 PM"), "5:00 PM")
        self.assertEqual(find_time("11/24/2026 - 1:20pm"), "1:20 PM")
        self.assertEqual(find_time("2026-11-17T17:00:00Z"), "5:00 PM")   # CivicClerk wall time
        self.assertEqual(find_time("20261117T180000"), "6:00 PM")         # iCal
        self.assertEqual(find_time("2026-11-17T00:00:00Z"), "")           # no time given
        self.assertEqual(find_time("Agenda 2026"), "")


class Export(unittest.TestCase):
    def test_filters(self):
        self.assertTrue(is_hub_event(_it("Planning Commission", date(2026, 11, 19)), TODAY))
        self.assertFalse(is_hub_event(_it("Planning Commission", date(2026, 11, 1)), TODAY))     # past
        self.assertFalse(is_hub_event(_it("BOC Meeting", date(2026, 11, 19), kind="minutes"), TODAY))
        self.assertFalse(is_hub_event(_it("Library Board", date(2026, 11, 19), meeting_body="Library Board"), TODAY))
        self.assertFalse(is_hub_event(_it("Senior Aerobics", date(2026, 11, 19), kind="event"), TODAY))
        self.assertFalse(is_hub_event(_it("BOC Meeting - CANCELLED", date(2026, 11, 19), tags=["noise"]), TODAY))

    def test_merges_same_meeting_from_two_feeds(self):
        d = date(2026, 11, 17)
        cal = _it("Board of Commissioners Meeting", d, kind="event", source="civicplus.calendar",
                  event_time="5:00 PM", location="150 East Washington Street")
        agenda = _it("11-17-2026 Board of Commissioners Meeting (Board of Commissioners)", d,
                     doc_url="https://x/a.pdf", meeting_body="Board of Commissioners",
                     tags=["development"], highlights=["Rezoning Parcels 062A-005 from R1 to CG"], score=6)
        (ev,) = build_events([cal, agenda], {"Morgan": "Morgan"}, TODAY)
        self.assertEqual(ev["time"], "5:00 PM")
        self.assertEqual(ev["venue"], "150 East Washington Street")
        self.assertEqual(ev["docUrl"], "https://x/a.pdf")
        self.assertEqual(ev["agenda"], "Rezoning Parcels 062A-005 from R1 to CG")
        self.assertEqual(ev["name"], "Morgan County Board of Commissioners Meeting")
        self.assertEqual(ev["reason"], "Land-use or development items are on the agenda.")
        self.assertEqual((ev["town"], ev["origin"], ev["cost"], ev["leads"]), ("Madison", "county", "Free", []))
        self.assertEqual(len(ev["parts"]), 5)

    def test_venue_cleanup(self):
        from scraper.hub_export import _venue
        cases = [("535 Telfair StAugusta, GA 30901", "Augusta", "535 Telfair St"),
                 ("119 Jones StreeetSandersville, GA 31082", "Sandersville", "119 Jones Streeet"),
                 ("Putnam County Administration Building Room 203Eatonton", "Eatonton",
                  "Putnam County Administration Building Room 203"),
                 ("Commission Chambers 100 Bacon Street Irwinton, GA", "Irwinton",
                  "Commission Chambers 100 Bacon Street"),
                 ("7635 Macon Hwy, Watkinsville, Watkinsville", "Watkinsville", "7635 Macon Hwy"),
                 ("", "Perry", "Houston County government offices")]
        for loc, town, want in cases:
            with self.subTest(loc=loc):
                self.assertEqual(_venue(loc, town, "Houston"), want)

    def test_types_and_canonical_county(self):
        evs = build_events([
            _it("Town Hall on the Comprehensive Plan", date(2026, 11, 20), county="Macon-Bibb", kind="event"),
            _it("Groundbreaking: Industrial Park", date(2026, 11, 21), county="Macon-Bibb", kind="event"),
        ], {"Macon-Bibb": "Bibb"}, TODAY)
        self.assertEqual([e["type"] for e in evs], ["Community forum", "Project milestone"])
        self.assertTrue(all(e["county"] == "Bibb" and e["town"] == "Macon" for e in evs))
        self.assertEqual(evs[0]["time"], "Time TBD")

    def test_write_js(self):
        evs = build_events([_it("Planning Commission", date(2026, 11, 19))], {"Morgan": "Morgan"}, TODAY)
        with tempfile.TemporaryDirectory() as d:
            p = write_js(evs, Path(d) / "data" / "county-events.js", datetime(2026, 11, 13, 15, 0))
            text = p.read_text()
        self.assertIn("window.COUNTY_EVENTS = ", text)
        payload = text.split("window.COUNTY_EVENTS = ", 1)[1].rstrip().rstrip(";")
        self.assertEqual(json.loads(payload)[0]["name"], "Morgan County Planning Commission")


if __name__ == "__main__":
    unittest.main()
