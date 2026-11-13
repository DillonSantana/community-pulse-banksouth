"""Generic fallback: read links off ordinary HTML pages (Revize, Squarespace, custom PHP...).

Config:
  pages          list of page URLs
  link_pattern   regex an href must match (default: documents - pdf/doc/DocumentCenter)
  match          regex the link text or href must match (default: meeting-ish words)
  undated_limit  keep up to N links with no parseable date (page order, default 0)
  kind           force a kind for every item (else guessed from the title)
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin

from ..dates import find_date
from ..models import Item
from .base import Context, clean, kind_from_title, soup

DOC_LINK = r"\.(pdf|docx?)($|\?)|DocumentCenter/View|/uploads/|ViewFile"
MEETING_WORDS = (r"agenda|minutes|meeting|hearing|notice|session|packet|zoning|rezon|"
                 r"develop|plan|annex|variance|forum|town hall|workshop|bid|rfp|rfq")


def fetch(ctx: Context, cfg: dict) -> list[Item]:
    link_rx = re.compile(cfg.get("link_pattern", DOC_LINK), re.I)
    match_rx = re.compile(cfg.get("match", MEETING_WORDS), re.I)
    undated_left = cfg.get("undated_limit", 0)

    def page_items(page_url: str) -> list[Item]:
        nonlocal undated_left
        out, seen = [], set()
        for a in soup(ctx.client.text(page_url)).find_all("a", href=True):
            href = a["href"].strip()
            if not link_rx.search(href):
                continue
            url = urljoin(page_url, href)
            text = clean(a.get_text(" ", strip=True), 300)
            fname = unquote(url.rsplit("/", 1)[-1].split("?")[0])
            label = text if len(text) > 3 else fname
            # Generic labels like "Agenda" get context from the surrounding row/list item.
            if len(label) < 25:
                parent = a.find_parent(["tr", "li", "p"])
                if parent:
                    ctx_text = clean(parent.get_text(" ", strip=True), 300)
                    if ctx_text and ctx_text != label:
                        label = f"{label} - {ctx_text}" if label else ctx_text
            if not match_rx.search(f"{label} {unquote(url)}") or url in seen:
                continue
            seen.add(url)
            when = find_date(label, unquote(url))
            if when is None:
                if undated_left <= 0:
                    continue
                undated_left -= 1
            elif not ctx.in_window(when):
                continue
            is_doc = bool(re.search(DOC_LINK, url, re.I))
            out.append(ctx.item(
                kind=cfg.get("kind") or kind_from_title(f"{label} {fname}"),
                title=label, url=url, doc_url=url if is_doc else "",
                event_date=when, source="html_docs",
            ))
        return out

    return ctx.run_parts("html_docs", [
        (f"page{i}", lambda p=p: page_items(p)) for i, p in enumerate(cfg["pages"])
    ])
