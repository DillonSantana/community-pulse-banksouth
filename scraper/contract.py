"""Lead Finder handoff: scrape_run + document records as JSON Lines.

Follows scraper_handoff.md:
  - one scrape_run per county per run, even when it found nothing or failed
  - one document per file, with source_url pointing at the file itself
  - text kept exactly as extracted (never cleaned or split)
  - dedupe on (source_url, content_hash); a changed hash is re-sent as a new record
  - canonical county name + FIPS on every record
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .documents import Doc
from .models import Item

SCRAPER_VERSION = "0.2.0"

DOC_TYPE = {"agenda": "agenda", "packet": "agenda_packet", "minutes": "minutes"}

# Boards the lead finder doesn't need (handoff section 2). Matched against the board
# name and title; anything not clearly unrelated is kept (as doc_type "other" if unsure).
UNRELATED_BOARDS = re.compile(
    r"librar|hospital|board of (elections|registrars)|election board|elections? (board|office)|"
    r"assessors?|pension|defined contribution|retirement|transit|animal|ethics|"
    r"personnel|recreation|parks|cemetery|senior|youth|health board|board of health|"
    r"family (connection|counseling)|DFCS|juvenile",
    re.I,
)
# Html notices/news worth sending as "other" documents.
HTML_NOTICE_TAGS = {"development", "economic_development", "community_forum"}


@dataclass
class CountyRun:
    county: str               # canonical, e.g. "Bibb"
    fips: str
    started_at: str
    scrape_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    finished_at: str = ""
    status: str = "running"
    docs_found: int = 0
    docs_new: int = 0
    errors: list[str] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)

    def record(self) -> dict:
        rec = {
            "record_type": "scrape_run",
            "scrape_run_id": self.scrape_run_id,
            "county": self.county,
            "county_fips": self.fips,
            "started_at": self.started_at,
            "finished_at": self.finished_at or None,
            "status": self.status,
            "docs_found": self.docs_found,
            "docs_new": self.docs_new,
            "scraper_version": SCRAPER_VERSION,
        }
        if self.status in ("partial", "failed"):
            rec["error_message"] = "; ".join(self.errors)[:2000] or "unknown error"
        return {k: v for k, v in rec.items() if v is not None}


def is_unrelated(item: Item) -> bool:
    return bool(UNRELATED_BOARDS.search(f"{item.meeting_body} {item.title}"))


def candidate_files(items: list[Item]) -> list[tuple[Item, str, str]]:
    """(item, url, kind) for every file the contract wants, deduped by URL."""
    out, seen = [], set()
    for it in items:
        if is_unrelated(it):
            continue
        files = []
        if it.doc_url:
            files.append((it.doc_url, it.kind))
        files += [(a["url"], a["kind"]) for a in it.attachments]
        if not files and it.kind in ("news", "notice") and HTML_NOTICE_TAGS & set(it.tags) \
                and "noise" not in it.tags:
            files.append((it.url, "other"))
        for url, kind in files:
            if url and url not in seen:
                seen.add(url)
                out.append((it, url, kind))
    return out


def doc_type_for(item: Item, kind: str) -> str:
    if kind in DOC_TYPE:
        return DOC_TYPE[kind]
    # Unlabelled files from a meeting: guess from the title, else "other".
    guess = DOC_TYPE.get(item.kind)
    if guess and kind in ("event", "document"):
        return guess
    return "other"


def document_record(run: CountyRun, item: Item, kind: str, doc: Doc) -> dict:
    rec = {
        "record_type": "document",
        "scrape_run_id": run.scrape_run_id,
        "county": run.county,
        "county_fips": run.fips,
        "doc_type": doc_type_for(item, kind),
        "source_url": doc.source_url,
        "retrieved_at": doc.retrieved_at,
        "content_hash": doc.content_hash,
        "file_type": doc.file_type,
        "raw_text": doc.raw_text,
        "file_ref": doc.file_ref,
        "has_text_layer": doc.has_text_layer,
        "meeting_body_raw": item.meeting_body or None,
        "meeting_date": item.event_date.isoformat() if item.event_date and item.kind != "news" else None,
        "extra": {
            **doc.extra,
            "title": item.title,
            "listing_url": item.url,
            "platform": item.source,
            "scraper_tags": item.tags,
        },
    }
    return {k: v for k, v in rec.items() if v is not None}


def write_jsonl(runs: list[CountyRun], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for run in sorted(runs, key=lambda r: r.county):
            f.write(json.dumps(run.record(), ensure_ascii=False) + "\n")
            for doc in run.documents:
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    return path
