"""Helpers shared by every adapter."""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator, Optional

from bs4 import BeautifulSoup

from ..http import Client
from ..models import Item


@dataclass
class Context:
    """What an adapter needs to know about the county it is scraping."""
    client: Client
    county: str
    region: str
    start: date          # earliest date we care about
    end: date            # latest date we care about
    errors: list[str] = field(default_factory=list)
    parts_ok: int = 0      # sub-scrapers that completed (drives succeeded/partial/failed)

    def run_parts(self, prefix: str, parts: list[tuple[str, Callable[[], list[Item]]]]) -> list[Item]:
        """Run each sub-scraper; record failures instead of aborting the county."""
        items: list[Item] = []
        for name, fn in parts:
            try:
                items += fn()
                self.parts_ok += 1
            except Exception as e:
                msg = f"{prefix}.{name}: {type(e).__name__}: {e}"
                logging.getLogger(__name__).warning("%s %s", self.county, msg)
                self.errors.append(msg[:300])
        return items

    def in_window(self, *candidates: Optional[date]) -> bool:
        """True if the first known date is in the window, or no date is known."""
        for d in candidates:
            if d is not None:
                return self.start <= d <= self.end
        return True

    def item(self, **kw) -> Item:
        return Item(county=self.county, region=self.region, **kw)


_BARE_AMP = re.compile(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)")
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean(text: str | None, limit: int = 2000) -> str:
    """Strip HTML and entities, collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(_TAGS.sub(" ", text))
    return _WS.sub(" ", text).strip()[:limit]


def soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, "html.parser")


def kind_from_title(title: str, default: str = "document") -> str:
    t = title.lower()
    if "packet" in t:
        return "packet"
    if "minute" in t:
        return "minutes"
    if "agenda" in t:
        return "agenda"
    if re.search(r"\bnotice\b|public hearing|legal ad", t):
        return "notice"
    return default


def rss_items(xml_text: str) -> Iterator[dict]:
    """Yield RSS <item>s as {tag-localname: text} dicts (namespaces stripped)."""
    xml_text = xml_text.lstrip("﻿").strip()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Some CivicPlus feeds contain bare "&" or pasted control characters
        # (e.g. \x02 from Word). Clean both and retry.
        root = ET.fromstring(_BARE_AMP.sub("&amp;", _BAD_XML_CHARS.sub("", xml_text)))
    for node in root.iter("item"):
        out: dict[str, str] = {}
        for child in node:
            name = child.tag.split("}")[-1]
            if name == "enclosure":
                out.setdefault("enclosure", child.get("url", ""))
            else:
                out.setdefault(name, (child.text or "").strip())
        yield out
