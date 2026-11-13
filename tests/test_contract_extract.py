"""Offline tests for the Lead Finder contract records and agenda-item extraction."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scraper.contract import CountyRun, candidate_files, document_record, is_unrelated, write_jsonl
from scraper.documents import Doc
from scraper.extract import extract_items, parcel_ids, parties, segments
from scraper.models import Item
from scraper.validate import validate_file

HASH = "a" * 64


def _doc_rec(text, doc_type="agenda", has_layer=True):
    return {"record_type": "document", "scrape_run_id": "00000000-0000-4000-8000-000000000000",
            "county": "Greene", "county_fips": "13133", "doc_type": doc_type,
            "source_url": "https://x/a.pdf", "content_hash": HASH, "raw_text": text,
            "has_text_layer": has_layer, "meeting_date": "2026-11-10"}


# Real agenda wording from Burke, Greene and Morgan county postings (Nov 2026).
BURKE = """AGENDA
COMMISSIONERS' MEETING
13. Bid Award - Police Pursuit Pickup Truck(s) - Sheriff's Office
14. Rezoning Parcels 062A-005 and 062A-005A from R1 to CG
15. Consideration of Resolution Concerning the Proposed Annexation by the City of
Hephzibah
"""
GREENE = """E. Approval of Minutes
 1. Minutes from the October 13, 2026 Regular Monthly Meeting.
F. Zoning & Subdivision Item
 1. Amendment to the Preliminary Plat for Lake Society, 317.873 acres, containing
559 dwellings - Atwell, LLC (Applicant)
 2. Final Plat for The Bluffs at Carey Station, Phase 2, 31.02 acres, 85 Lots — Dream
Finders Homes (Applicant).
G. Business Items
 1. Development Agreement with Red Clay Development Group, LLC (Traffic
Light)
"""
MORGAN = """NEW BUSINESS:

I. Kevin Courchaine, on behalf of Burnt Pine Farm, LLC, Burnt Pine Eat, LLC and GA Atlantic
Development, is requesting a text amendment to the Morgan County Zoning Ordinance to Articles 3,
4 and 7.
II. Morgan County Planning and Development is requesting a text amendment to the Morgan County
Development Regulations to modify language and organization of the development regulations.
WITHDRAWN
"""
MINUTES = """3. Rezoning request by 531 Greene, LLC for 519.69 acres on Madison Hwy, Map 045 Parcel 012,
from A-1 (Agricultural) to PUD. Commissioner Smith made a motion to approve; the motion carried 4-1.
"""


class Extraction(unittest.TestCase):
    def test_burke_rezoning(self):
        items = extract_items(_doc_rec(BURKE))
        rz = next(i for i in items if i["request_type"] == "rezoning")
        self.assertEqual(rz["parcel_ids_raw"], ["062A-005", "062A-005A"])
        self.assertEqual((rz["zoning_from"], rz["zoning_to"]), ("R1", "CG"))
        self.assertIsNone(rz["outcome"])                  # an agenda states no outcome
        # Offsets point at the exact words in the document, for citations.
        text = BURKE
        self.assertTrue(text[rz["char_start"]:rz["char_end"]].strip().startswith("14. Rezoning"))
        self.assertTrue(any(i["request_type"] == "annexation" for i in items))
        self.assertFalse(any("Bid Award" in i["item_text"] for i in items))

    def test_greene_plats_and_sections(self):
        items = extract_items(_doc_rec(GREENE))
        by_type = {i["request_type"]: i for i in items}
        lake = by_type["plat_preliminary"]
        self.assertEqual(lake["acreage"], 317.873)
        self.assertEqual(lake["units"], 559)
        self.assertIn({"name_raw": "Atwell, LLC", "role": "applicant"}, lake["parties"])
        self.assertEqual(lake["section_raw"], "Zoning & Subdivision Item")
        bluffs = by_type["plat_final"]
        self.assertIn({"name_raw": "Dream Finders Homes", "role": "applicant"}, bluffs["parties"])
        dev = by_type["development_agreement"]
        self.assertEqual(dev["parties"], [{"name_raw": "Red Clay Development Group, LLC", "role": "counterparty"}])
        self.assertNotIn("plat", [i["request_type"] for i in items if "Minutes from" in i["item_text"]])

    def test_agent_for_owner_and_withdrawn(self):
        items = extract_items(_doc_rec(MORGAN))
        first, second = items
        roles = {(p["name_raw"], p["role"]) for p in first["parties"]}
        self.assertIn(("Kevin Courchaine", "agent"), roles)
        self.assertIn(("Burnt Pine Farm, LLC", "owner"), roles)
        self.assertIn(("Burnt Pine Eat, LLC", "owner"), roles)
        self.assertEqual(first["section_raw"], "NEW BUSINESS")
        self.assertEqual(second["outcome"], "withdrawn")
        self.assertEqual(first["outcome"], None)

    def test_minutes_outcome_and_map_parcel(self):
        (item,) = extract_items(_doc_rec(MINUTES, doc_type="minutes"))
        self.assertEqual(item["request_type"], "rezoning")
        self.assertEqual(item["acreage"], 519.69)
        self.assertIn("Map 045 Parcel 012", item["parcel_ids_raw"])
        self.assertEqual((item["zoning_from"], item["zoning_to"]), ("A-1", "PUD"))
        self.assertEqual(item["outcome"], "approved")
        self.assertIn("motion carried", item["outcome_evidence"].replace("the ", ""))
        self.assertIn({"name_raw": "531 Greene, LLC", "role": "applicant"}, item["parties"])

    def test_scanned_document_flagged(self):
        (item,) = extract_items(_doc_rec(MINUTES, has_layer=False))
        self.assertTrue(item["needs_review"])
        self.assertIn("document has no text layer", item["review_reasons"])

    def test_ids_are_stable(self):
        a = extract_items(_doc_rec(BURKE))
        b = extract_items(_doc_rec(BURKE))
        self.assertEqual([i["agenda_item_id"] for i in a], [i["agenda_item_id"] for i in b])

    def test_parcel_ids_skip_dates_and_words(self):
        self.assertEqual(parcel_ids("Parcel ID 056-0-00-007-0 on 11-10-2026"), ["056-0-00-007-0"])
        self.assertEqual(parcel_ids("tax parcels 062A-005, 062A-005A and C12-034 owned by"),
                         ["062A-005", "062A-005A", "C12-034"])

    def test_generic_words_are_not_parties(self):
        names = [p["name_raw"] for p in parties("Extension of the Planned Unit Development Moratorium")]
        self.assertEqual(names, [])

    def test_paragraph_fallback(self):
        segs = segments("NOTICE OF PUBLIC HEARING\n\nA request to rezone 12 acres.\n\nSigned.")
        self.assertEqual(len(segs), 3)


def _item(**kw):
    base = dict(county="Greene", region="R", kind="agenda", title="BOC Meeting",
                url="https://x/event/1", source="civicclerk", event_date=date(2026, 11, 10))
    base.update(kw)
    return Item(**base)


class Contract(unittest.TestCase):
    def test_candidate_files_skip_unrelated_and_include_attachments(self):
        boc = _item(doc_url="https://x/agenda.pdf", meeting_body="Board of Commissioners",
                    attachments=[{"kind": "packet", "url": "https://x/packet.pdf", "name": "p"}])
        lib = _item(title="Library Board", doc_url="https://x/lib.pdf", meeting_body="Library Board")
        news = _item(kind="news", title="Costco approved", url="https://x/news", tags=["development"])
        noise = _item(kind="news", title="Closed", url="https://x/closed", tags=["development", "noise"])
        files = candidate_files([boc, lib, news, noise])
        self.assertEqual([(u, k) for _, u, k in files],
                         [("https://x/agenda.pdf", "agenda"), ("https://x/packet.pdf", "packet"),
                          ("https://x/news", "other")])
        self.assertTrue(is_unrelated(lib))

    def test_records_validate(self):
        run = CountyRun(county="Greene", fips="13133", started_at="2026-11-13T14:00:00-05:00",
                        finished_at="2026-11-13T14:01:00-05:00", status="succeeded", docs_found=1, docs_new=1)
        doc = Doc(source_url="https://x/agenda.pdf", content_hash=HASH, file_type="pdf",
                  retrieved_at="2026-11-13T14:00:30-05:00", file_ref="data/files/a.pdf",
                  raw_text="AGENDA", has_text_layer=True, changed=True)
        run.documents.append(document_record(run, _item(doc_url=doc.source_url,
                                                        meeting_body="Board of Commissioners"), "packet", doc))
        failed = CountyRun(county="Johnson", fips="13167", started_at="2026-11-13T14:00:00-05:00",
                           status="failed", errors=["HTTP 403"])
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl([run, failed], Path(d) / "s.jsonl")
            self.assertEqual(validate_file(path), [])
            lines = [json.loads(x) for x in path.read_text().splitlines()]
        self.assertEqual(lines[1]["doc_type"], "agenda_packet")
        self.assertEqual(lines[1]["meeting_body_raw"], "Board of Commissioners")
        self.assertEqual(lines[2]["error_message"], "HTTP 403")

    def test_validator_catches_problems(self):
        bad = [
            {"record_type": "scrape_run", "scrape_run_id": "00000000-0000-4000-8000-000000000000",
             "county": "Greene County", "county_fips": "13133", "started_at": "2026-11-13T14:00:00-05:00",
             "status": "failed"},
            {"record_type": "document", "scrape_run_id": "00000000-0000-4000-8000-000000000000",
             "county": "Morgan", "county_fips": "13211", "doc_type": "agenda", "source_url": "https://x",
             "retrieved_at": "2026-11-13", "content_hash": "ABC", "file_type": "pdf"},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.jsonl"
            p.write_text("\n".join(json.dumps(b) for b in bad))
            problems = "\n".join(validate_file(p))
        for expected in ("error_message", "county", "content_hash", "retrieved_at", "doesn't match its run"):
            self.assertIn(expected, problems)


if __name__ == "__main__":
    unittest.main()
