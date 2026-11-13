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
        # Real offsets are converted to Eastern; Eastern midnight means "no time given".
        self.assertEqual(find_time("2026-11-17T23:00:00Z"), "6:00 PM")
        self.assertEqual(find_time("2026-12-14T05:00:00+00:00"), "")      # Pulaski, date-only
        self.assertEqual(find_time("2026-07-14T22:30:00+00:00"), "6:30 PM")  # summer, EDT
        self.assertEqual(find_time("2026-11-17T18:00:00-05:00"), "6:00 PM")
        self.assertEqual(find_time("20261117T180000"), "6:00 PM")         # iCal, local
        self.assertEqual(find_time("20261117T230000Z"), "6:00 PM")        # iCal, UTC
        self.assertEqual(find_time("2026-11-17T00:00:00"), "")            # no time given

    def test_mec_time_comes_from_event_page(self):
        # MEC's JSON-LD says 05:00-05:00 for a 10 AM meeting; the page shows the truth.
        from scraper.adapters.base import Context
        from scraper.adapters.wordpress import events_pages
        listing = ("<link href='/wp-content/plugins/modern-events-calendar-lite/x.css'>"
                   '<script type="application/ld+json">{"@type":"Event","name":"Pulaski County Commissioner Meeting",'
                   '"startDate":"2026-12-14T05:00:00-05:00","url":"https://x/ev/1"}</script>'
                   '<script type="application/ld+json">{"@type":"Event","name":"Holiday",'
                   '"startDate":"2026-12-24","url":"https://x/ev/2"}</script>')
        pages = {"https://x/events": listing,
                 "https://x/ev/1": '<div class="mec-single-event-time"><dd>10:00 am - 11:00 am</dd></div>'}

        class FakeClient:
            def text(self, url):
                return pages[url]

        ctx = Context(client=FakeClient(), county="Pulaski", region="R",
                      start=date(2026, 11, 1), end=date(2027, 2, 1))
        evs = {e.title: e.event_time for e in events_pages(ctx, ["https://x/events"])}
        self.assertEqual(evs["Pulaski County Commissioner Meeting"], "10:00 AM")
        self.assertEqual(evs["Holiday"], "")

    def test_civicclerk_wall_time(self):
        # CivicClerk sends local times labeled Z; the adapter strips the offset first.
        from scraper.dates import parse_iso, strip_offset
        self.assertEqual(find_time(strip_offset("2026-11-17T17:00:00Z")), "5:00 PM")
        self.assertEqual(parse_iso(strip_offset("2026-11-17T00:00:00Z")), date(2026, 11, 17))

    def test_offset_dates_use_eastern(self):
        from scraper.dates import parse_iso
        # 8 PM Eastern on the 17th is 01:00 UTC on the 18th.
        self.assertEqual(parse_iso("2026-11-18T01:00:00Z"), date(2026, 11, 17))
        self.assertEqual(parse_iso("2026-11-17"), date(2026, 11, 17))
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

    def test_titles(self):
        from scraper.hub_export import _clean_title
        it = lambda title, county="Wilkinson": _it(title, date(2026, 11, 19), county=county)
        self.assertEqual(_clean_title(it("Work-Session-Agenda"), "Wilkinson"), "Wilkinson County Work Session")
        self.assertEqual(_clean_title(it("City of Hawkinsville Commission Meeting", "Pulaski"), "Pulaski"),
                         "City of Hawkinsville Commission Meeting")

    def test_does_not_mutate_input_items(self):
        d = date(2026, 11, 17)
        a = _it("Board of Commissioners Meeting", d, kind="event", event_time="5:00 PM")
        b = _it("Board of Commissioners Meeting", d, doc_url="https://x/a.pdf", tags=["development"], score=6)
        build_events([a, b], {"Morgan": "Morgan"}, TODAY)
        self.assertEqual((b.event_time, a.doc_url, a.tags), ("", "", []))

    def test_untitled_meetings_stay_separate(self):
        d = date(2026, 11, 17)
        evs = build_events([_it("Meeting Agenda", d, url="https://x/1.pdf"),
                            _it("Meeting", d, url="https://x/2.pdf")], {"Morgan": "Morgan"}, TODAY)
        self.assertEqual(len(evs), 2)

    def test_updated_label_is_portable(self):
        (ev,) = build_events([_it("Planning Commission", date(2026, 11, 19))], {"Morgan": "Morgan"}, date(2026, 11, 3))
        self.assertEqual(ev["updated"], "3 Nov")

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
