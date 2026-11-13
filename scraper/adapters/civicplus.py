"""CivicPlus county sites: AgendaCenter page, Calendar RSS and News Flash RSS.

Config keys (all optional, default True): agenda_center, calendar, news.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ..dates import find_date, parse_rfc822
from ..models import Item
from .base import Context, clean, kind_from_title, rss_items, soup

VIEWFILE = re.compile(r"/AgendaCenter/ViewFile/(Agenda|Minutes)/_(\d{8})-(\d+)", re.I)


def agenda_center(ctx: Context, base: str) -> list[Item]:
    page = soup(ctx.client.text(urljoin(base, "/AgendaCenter")))
    items: dict[str, Item] = {}
    for a in page.find_all("a", href=VIEWFILE):
        m = VIEWFILE.search(a["href"])
        doc_type, stamp = m.group(1).lower(), m.group(2)
        when = find_date("_" + stamp + "-0")
        if not ctx.in_window(when):
            continue
        url = urljoin(base, m.group(0))
        row = a.find_parent("tr")
        # The first link in the row carries the meeting title; "Agenda" is the button.
        title = ""
        if row:
            for link in row.find_all("a", href=VIEWFILE):
                txt = link.get_text(" ", strip=True)
                if txt and txt.lower() not in ("agenda", "minutes", "html", "pdf"):
                    title = txt
                    break
        heading = a.find_previous("h2")   # h3s in the row hold the date, h2 is the board
        category = heading.get_text(" ", strip=True) if heading else ""
        title = title or f"{category} {doc_type}".strip()
        kind = "minutes" if doc_type == "minutes" else "agenda"
        if url in items:
            continue
        items[url] = ctx.item(
            kind=kind,
            title=f"{title} ({category})" if category and category not in title else title,
            url=url, doc_url=url, event_date=when, meeting_body=category,
            source="civicplus.agendacenter",
        )
    return list(items.values())


def calendar_rss(ctx: Context, base: str) -> list[Item]:
    xml = ctx.client.text(urljoin(base, "/RSSFeed.aspx?ModID=58&CID=All-calendar.xml"))
    out = []
    for it in rss_items(xml):
        when = find_date(it.get("EventDates", ""), clean(it.get("description")))
        if not ctx.in_window(when):
            continue
        out.append(ctx.item(
            kind="event",
            title=clean(it.get("title"), 300),
            url=it.get("link", ""),
            event_date=when,
            posted_date=parse_rfc822(it.get("pubDate")),
            location=clean(it.get("Location"), 300),
            body=clean(it.get("description")),
            source="civicplus.calendar",
        ))
    return out


def news_rss(ctx: Context, base: str) -> list[Item]:
    xml = ctx.client.text(urljoin(base, "/RSSFeed.aspx?ModID=1&CID=All-newsflash.xml"))
    out = []
    for it in rss_items(xml):
        posted = parse_rfc822(it.get("pubDate"))
        title = clean(it.get("title"), 300)
        body = clean(it.get("description"))
        if not ctx.in_window(posted):
            continue
        out.append(ctx.item(
            kind=kind_from_title(title, "news"),
            title=title, url=it.get("link", ""),
            posted_date=posted, event_date=find_date(title),
            body=body, source="civicplus.news",
        ))
    return out


def fetch(ctx: Context, cfg: dict) -> list[Item]:
    base = cfg["base"]
    parts = [("agenda_center", agenda_center), ("calendar", calendar_rss), ("news", news_rss)]
    return ctx.run_parts("civicplus", [
        (key, lambda fn=fn: fn(ctx, base)) for key, fn in parts if cfg.get(key, True)
    ])
