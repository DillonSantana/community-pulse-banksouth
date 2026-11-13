"""WordPress county sites via the public REST API, plus event-page structured data.

Config:
  base          site root
  posts         bool (default True)  - news/notice posts
  media         bool (default True)  - uploaded PDFs (agendas, minutes, notices)
  events_pages  list of URLs whose JSON-LD Event blocks we read (MEC, etc.)
  ical          URL of a Tribe Events iCal feed (e.g. /events/?ical=1)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from ..dates import find_date, parse_iso
from ..models import Item
from .base import Context, clean, kind_from_title, soup


def _api(ctx: Context, base: str, route: str, params: dict, max_pages: int = 4) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = ctx.client.get(urljoin(base, "/wp-json/wp/v2/" + route), params={**params, "page": page})
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"unexpected {route} response: {str(data)[:120]}")
        rows += data
        if page >= int(resp.headers.get("X-WP-TotalPages", "1") or 1):
            break
    return rows


def posts(ctx: Context, base: str) -> list[Item]:
    rows = _api(ctx, base, "posts", {
        "per_page": 50, "orderby": "date", "after": f"{ctx.start.isoformat()}T00:00:00",
        "_fields": "date,link,title,excerpt",
    })
    out = []
    for p in rows:
        title = clean(p["title"]["rendered"], 300)
        posted = parse_iso(p.get("date"))
        out.append(ctx.item(
            kind=kind_from_title(title, "news"), title=title, url=p["link"],
            posted_date=posted, event_date=find_date(title),
            body=clean((p.get("excerpt") or {}).get("rendered")), source="wordpress.posts",
        ))
    return out


def media(ctx: Context, base: str) -> list[Item]:
    rows = _api(ctx, base, "media", {
        "per_page": 100, "orderby": "date", "mime_type": "application/pdf",
        "after": f"{ctx.start.isoformat()}T00:00:00",
        "_fields": "date,title,source_url",
    })
    out = []
    for m in rows:
        title = clean(m["title"]["rendered"], 300)
        src = m.get("source_url", "")
        when = find_date(title, src.rsplit("/", 1)[-1])
        posted = parse_iso(m.get("date"))
        if not ctx.in_window(when, posted):
            continue
        out.append(ctx.item(
            kind=kind_from_title(title), title=title, url=src, doc_url=src,
            event_date=when, posted_date=posted, source="wordpress.media",
        ))
    return out


def _jsonld_events(markup: str):
    for block in soup(markup).find_all("script", type="application/ld+json"):
        try:
            data = json.loads(block.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@type") == "Event":
                    yield node
                stack.extend(node.get("@graph", []) if isinstance(node.get("@graph"), list) else [])
            elif isinstance(node, list):
                stack.extend(node)


def events_pages(ctx: Context, pages: list[str]) -> list[Item]:
    out, seen = [], set()
    for page in pages:
        for ev in _jsonld_events(ctx.client.text(page)):
            when = parse_iso(ev.get("startDate"))
            url = ev.get("url") or page
            key = (ev.get("name"), str(when), url)
            if key in seen or not ctx.in_window(when):
                continue
            seen.add(key)
            loc = ev.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = loc.get("address") if isinstance(loc, dict) else ""
            if isinstance(addr, dict):
                addr = " ".join(str(v) for k, v in addr.items() if not k.startswith("@"))
            location = clean(f"{loc.get('name', '') if isinstance(loc, dict) else ''} {addr or ''}", 300)
            out.append(ctx.item(
                kind="event", title=clean(ev.get("name"), 300), url=url,
                event_date=when, location=location,
                body=clean(ev.get("description")), source="wordpress.events",
            ))
    return out


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def ical(ctx: Context, url: str) -> list[Item]:
    out, cur = [], None
    for line in _unfold_ics(ctx.client.text(url)):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            raw = cur.get("DTSTART", "")
            when = None
            m = re.match(r"(\d{8})", raw)
            if m:
                when = datetime.strptime(m.group(1), "%Y%m%d").date()
            if ctx.in_window(when):
                desc = cur.get("DESCRIPTION", "").replace("\\n", " ").replace("\\,", ",")
                out.append(ctx.item(
                    kind="event", title=clean(cur.get("SUMMARY", "").replace("\\,", ","), 300),
                    url=cur.get("URL", url), event_date=when,
                    location=clean(cur.get("LOCATION", "").replace("\\,", ","), 300),
                    body=clean(desc), source="wordpress.ical",
                ))
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            cur.setdefault(key.split(";")[0], val)
    return out


def fetch(ctx: Context, cfg: dict) -> list[Item]:
    base = cfg["base"]
    parts = []
    if cfg.get("posts", True):
        parts.append(("posts", lambda: posts(ctx, base)))
    if cfg.get("media", True):
        parts.append(("media", lambda: media(ctx, base)))
    if cfg.get("events_pages"):
        parts.append(("events", lambda: events_pages(ctx, cfg["events_pages"])))
    if cfg.get("ical"):
        parts.append(("ical", lambda: ical(ctx, cfg["ical"])))
    return ctx.run_parts("wordpress", parts)
