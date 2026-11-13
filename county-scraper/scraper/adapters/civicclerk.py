"""CivicClerk public API (used by Macon-Bibb, Greene, Oconee, Columbia).

Config: {"type": "civicclerk", "tenant": "maconbibbcoga"}
"""
from __future__ import annotations

from datetime import timedelta

from ..dates import find_time, parse_iso
from ..models import Item
from .base import Context, clean

API = "https://{t}.api.civicclerk.com/v1"
PORTAL = "https://{t}.portal.civicclerk.com/event/{id}/files"
FILE = API + "/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)"


def _pick_file(files: list[dict]) -> tuple[str, dict | None]:
    """Prefer the agenda, then the packet, then minutes."""
    for want, kind in (("agenda", "agenda"), ("packet", "packet"), ("minute", "minutes")):
        for f in files:
            label = f"{f.get('type', '')} {f.get('name', '')}".lower()
            if want in label and (want != "agenda" or "packet" not in label):
                return kind, f
    return "event", (files[0] if files else None)


def fetch(ctx: Context, cfg: dict) -> list[Item]:
    t = cfg["tenant"]

    def events() -> list[Item]:
        params = {
            "$filter": (f"startDateTime ge {ctx.start.isoformat()}T00:00:00Z and "
                        f"startDateTime lt {(ctx.end + timedelta(days=1)).isoformat()}T00:00:00Z"),
            "$orderby": "startDateTime",
            "$top": "300",
        }
        data = ctx.client.json(API.format(t=t) + "/Events", params=params)
        out = []
        for ev in data.get("value", []):
            when = parse_iso(ev.get("startDateTime"))
            if not ctx.in_window(when):
                continue
            files = ev.get("publishedFiles") or []
            kind, f = _pick_file(files)
            attachments = [
                {"kind": _pick_file([o])[0], "url": FILE.format(t=t, fid=o["fileId"]),
                 "name": o.get("name", "")}
                for o in files if o is not f and o.get("fileId")
            ]
            category = (ev.get("categoryName") or "").strip()
            loc = ev.get("eventLocation") or {}
            location = ", ".join(x for x in (loc.get("address1"), loc.get("address2"),
                                             loc.get("city")) if x)
            out.append(ctx.item(
                kind=kind if kind != "event" else "event",
                title=clean(ev.get("eventName"), 300),
                url=PORTAL.format(t=t, id=ev["id"]),
                doc_url=FILE.format(t=t, fid=f["fileId"]) if f and f.get("fileId") else "",
                event_date=when,
                event_time=find_time(ev.get("startDateTime") or ""),
                posted_date=parse_iso(ev.get("createdOn")),
                location=location,
                # "General" is CivicClerk's catch-all; the event name is more telling then.
                meeting_body=category if category and category.lower() != "general"
                else clean(ev.get("eventName"), 200),
                attachments=attachments,
                body=clean(f"{category}. {ev.get('eventDescription') or ''}"),
                source="civicclerk",
            ))
        return out

    return ctx.run_parts("civicclerk", [("events", events)])
