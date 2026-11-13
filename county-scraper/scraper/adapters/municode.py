"""Municode Meetings portals (Augusta-Richmond, Baldwin).

Config: {"type": "municode", "url": "https://augustarichmond-ga.municodemeetings.com/", "max_pages": 3}
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ..dates import find_date, find_time
from ..models import Item
from .base import Context, clean, soup

AGENDA = re.compile(r"MEET-Agenda-[0-9a-f]+\.pdf", re.I)
PACKET = re.compile(r"MEET-Packet-[0-9a-f]+\.pdf", re.I)


def fetch(ctx: Context, cfg: dict) -> list[Item]:
    def scrape() -> list[Item]:
        url, out, pages = cfg["url"], [], 0
        while url and pages < cfg.get("max_pages", 3):
            pages += 1
            page = soup(ctx.client.text(url))
            oldest = None
            for row in page.select("table tbody tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                when = find_date(cells[0].get_text(" ", strip=True))
                oldest = when if oldest is None or (when and when < oldest) else oldest
                if not ctx.in_window(when):
                    continue
                links = {a["href"]: a for a in row.find_all("a", href=True)}
                agenda = next((h for h in links if AGENDA.search(h)), "")
                packet = next((h for h in links if PACKET.search(h)), "")
                detail = next((h for h in links if "/page/" in h), "")
                title = clean(cells[1].get_text(" ", strip=True), 300)
                out.append(ctx.item(
                    kind="agenda" if agenda else ("packet" if packet else "event"),
                    title=title,
                    url=urljoin(url, detail) if detail else urljoin(url, agenda or packet or url),
                    doc_url=urljoin(url, agenda or packet) if (agenda or packet) else "",
                    attachments=[{"kind": "packet", "url": urljoin(url, packet), "name": title}]
                    if agenda and packet else [],
                    meeting_body=title,
                    event_date=when, event_time=find_time(cells[0].get_text(" ", strip=True)),
                    source="municode",
                ))
            nxt = page.select_one(".pager a[href*='page='], li.pager-next a")
            # Stop paging once the listing is older than our window.
            if not nxt or (oldest and oldest < ctx.start):
                break
            url = urljoin(url, nxt["href"])
        return out

    return ctx.run_parts("municode", [("meetings", scrape)])
