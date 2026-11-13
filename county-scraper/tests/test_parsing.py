"""Offline tests for the parsing helpers (no network). Run: .venv/bin/python -m unittest -v"""
import unittest
from datetime import date

from scraper.adapters.base import Context, kind_from_title, rss_items
from scraper.adapters.wordpress import _jsonld_events
from scraper.cli import dedupe
from scraper.dates import find_date, parse_iso, parse_rfc822
from scraper.models import Item
from scraper.tagging import highlights, tag_item


class Dates(unittest.TestCase):
    def test_formats(self):
        cases = {
            "11-17-2026 Agenda": date(2026, 11, 17),
            "Agenda 11/3/2026": date(2026, 11, 3),
            "Meeting 11.17.26": date(2026, 11, 17),
            "2026-11-17 Agenda.pdf": date(2026, 11, 17),
            "November 17, 2026": date(2026, 11, 17),
            "Nov. 5th 2026 called meeting": date(2026, 11, 5),
            "agenda_-_regular_meeting_-_january_13th_2026.pdf": date(2026, 1, 13),
            "/AgendaCenter/ViewFile/Agenda/_11032026-885": date(2026, 11, 3),
            "MeetingMinutesOctober152026": date(2026, 10, 15),
            "AGENDANOV52026": date(2026, 11, 5),
            "17 November 2026": date(2026, 11, 17),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(find_date(text), want)

    def test_rejects_garbage(self):
        for text in ("Agenda", "13-45-2026", "Ordinance 2026", "fileId=3772", "99/99/99"):
            with self.subTest(text=text):
                self.assertIsNone(find_date(text))

    def test_iso_and_rfc(self):
        self.assertEqual(parse_iso("2026-11-17T14:00:00Z"), date(2026, 11, 17))
        self.assertEqual(parse_rfc822("Wed, 11 Feb 2026 12:34:12 -0500"), date(2026, 2, 11))
        self.assertIsNone(parse_iso(None))


class Feeds(unittest.TestCase):
    def test_rss_with_namespace_and_bare_ampersand(self):
        xml = """<?xml version="1.0"?><rss xmlns:calendarEvent="urn:x"><channel>
        <item><title>Parks & Rec Meeting</title><description>well\x02being</description><link>https://x/Calendar.aspx?EID=1</link>
        <calendarEvent:EventDates> November 17, 2026 </calendarEvent:EventDates>
        <enclosure url="https://x/doc.png"/></item></channel></rss>"""
        items = list(rss_items(xml))
        self.assertEqual(items[0]["title"], "Parks & Rec Meeting")
        self.assertEqual(items[0]["EventDates"], "November 17, 2026")
        self.assertEqual(items[0]["enclosure"], "https://x/doc.png")

    def test_jsonld_events(self):
        html = """<script type="application/ld+json">{"@context":"http://schema.org","@type":"Event",
        "name":"Commissioners Meeting","startDate":"2026-11-17"}</script>
        <script type="application/ld+json">{"@graph":[{"@type":"Event","name":"Town Hall","startDate":"2026-12-01"}]}</script>
        <script type="application/ld+json">not json</script>"""
        names = sorted(e["name"] for e in _jsonld_events(html))
        self.assertEqual(names, ["Commissioners Meeting", "Town Hall"])

    def test_kind_from_title(self):
        self.assertEqual(kind_from_title("11-17-2026 Agenda"), "agenda")
        self.assertEqual(kind_from_title("Agenda Packet"), "packet")
        self.assertEqual(kind_from_title("Regular Meeting Minutes"), "minutes")
        self.assertEqual(kind_from_title("Notice of Public Hearing"), "notice")
        self.assertEqual(kind_from_title("Holiday schedule", "news"), "news")


def _item(title, **kw):
    return Item(county="Test", region="R", kind=kw.pop("kind", "agenda"), title=title,
                url=kw.pop("url", "https://x/" + title), source="t", **kw)


class Tagging(unittest.TestCase):
    def test_development_beats_noise(self):
        dev = tag_item(_item("Public Hearing: Rezoning request for data center site"))
        noise = tag_item(_item("Courthouse closed for Veterans Day holiday"))
        self.assertIn("development", dev.tags)
        self.assertIn("community_forum", dev.tags)
        self.assertIn("noise", noise.tags)
        self.assertGreater(dev.score, 6)
        self.assertLess(noise.score, 1)

    def test_word_boundaries(self):
        # "proposition" must not trip the job-posting noise rule; "granted" is not a grant.
        it = tag_item(_item("Proposition on industrial park expansion"))
        self.assertNotIn("noise", it.tags)
        self.assertNotIn("economic_development", tag_item(_item("Variance granted")).tags)

    def test_highlights_from_pdf_text(self):
        text = ("1. Call to order. 2. Invocation. "
                "3. Rezoning request by Smith Holdings for 120 acres on Hwy 44 from AG to M-1. "
                "4. Presentation by Georgia Power on the industrial park. 5. Adjourn.")
        hl = highlights(text)
        self.assertTrue(any("Rezoning request" in h for h in hl))
        self.assertTrue(any("Presentation by Georgia Power" in h for h in hl))
        self.assertFalse(any("Invocation" in h for h in hl))


class Window(unittest.TestCase):
    def test_in_window(self):
        ctx = Context(client=None, county="c", region="r", start=date(2026, 11, 1), end=date(2026, 12, 1))
        self.assertTrue(ctx.in_window(date(2026, 11, 15)))
        self.assertFalse(ctx.in_window(date(2026, 10, 1)))
        self.assertTrue(ctx.in_window(None))                       # unknown date -> keep
        self.assertFalse(ctx.in_window(None, date(2025, 1, 1)))    # falls through to 2nd date

    def test_dedupe_prefers_document_and_keeps_minutes(self):
        d = date(2026, 11, 3)
        a = _item("BOC Meeting", kind="event", event_date=d)
        b = _item("BOC  meeting", kind="event", event_date=d, doc_url="https://x/a.pdf", url="https://y")
        c = _item("BOC Meeting", kind="minutes", event_date=d)
        out = dedupe([a, b, c])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].doc_url, "https://x/a.pdf")
        self.assertEqual(out[1].kind, "minutes")


if __name__ == "__main__":
    unittest.main()
