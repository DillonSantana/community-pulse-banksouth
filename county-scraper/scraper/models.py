"""Shared data model for everything the adapters collect."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

# Kinds of item an adapter can emit.
KINDS = ("agenda", "minutes", "packet", "event", "news", "notice", "document")


@dataclass
class Item:
    county: str
    region: str
    kind: str                      # one of KINDS
    title: str
    url: str
    source: str                    # adapter name, e.g. "civicplus.agendacenter"
    event_date: Optional[date] = None   # when the meeting/event happens
    posted_date: Optional[date] = None  # when the county published it
    event_time: str = ""           # "6:00 PM" when the source gives one
    location: str = ""
    body: str = ""                 # description / extracted text (trimmed)
    doc_url: str = ""              # direct PDF link when we have one
    meeting_body: str = ""         # board name exactly as posted, when the source says
    # Other files for the same meeting (packet, minutes...): [{"kind", "url", "name"}]
    attachments: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    score: int = 0

    @property
    def uid(self) -> str:
        key = f"{self.county}|{self.url}|{self.title}".lower()
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def sort_date(self) -> Optional[date]:
        return self.event_date or self.posted_date

    def to_row(self) -> dict:
        d = asdict(self)
        d["uid"] = self.uid
        d["event_date"] = self.event_date.isoformat() if self.event_date else ""
        d["posted_date"] = self.posted_date.isoformat() if self.posted_date else ""
        d["tags"] = ",".join(self.tags)
        d["highlights"] = " || ".join(self.highlights)
        d.pop("attachments", None)
        return d
