"""Feed the Opportunity Hub's "Where to be" calendar (../index.html).

Turns upcoming public meetings from a scrape into the hub's event objects and writes
them as `window.COUNTY_EVENTS = [...]` in ../data/county-events.js. A <script> file
rather than JSON, so the hub still works when index.html is opened straight from disk
(browsers block fetch() on file:// pages).

Only real, upcoming, relevant meetings go out:
  - dated today or later
  - not cancelled, not routine noise (holidays, election results, rec programs)
  - not an unrelated board (library, elections, assessors...)
  - calendar entries must look like a meeting, hearing, forum or project milestone
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from .contract import is_unrelated
from .models import Item

# County seat (town, lat, lng) for placing meetings on the map and in distance filters.
SEATS: dict[str, tuple[str, float, float]] = {
    "Baldwin": ("Milledgeville", 33.080, -83.232), "Bibb": ("Macon", 32.840, -83.632),
    "Crawford": ("Knoxville", 32.724, -83.997), "Houston": ("Perry", 32.458, -83.731),
    "Jones": ("Gray", 33.009, -83.534), "Monroe": ("Forsyth", 33.034, -83.938),
    "Peach": ("Fort Valley", 32.554, -83.887), "Pulaski": ("Hawkinsville", 32.284, -83.472),
    "Putnam": ("Eatonton", 33.327, -83.389), "Twiggs": ("Jeffersonville", 32.687, -83.347),
    "Wilkinson": ("Irwinton", 32.811, -83.172), "Richmond": ("Augusta", 33.471, -81.975),
    "Columbia": ("Evans", 33.533, -82.130), "Burke": ("Waynesboro", 33.090, -82.016),
    "Glascock": ("Gibson", 33.234, -82.596), "Hancock": ("Sparta", 33.276, -82.976),
    "Jefferson": ("Louisville", 33.001, -82.411), "Jenkins": ("Millen", 32.804, -81.949),
    "Lincoln": ("Lincolnton", 33.792, -82.479), "McDuffie": ("Thomson", 33.471, -82.505),
    "Taliaferro": ("Crawfordville", 33.554, -82.896), "Warren": ("Warrenton", 33.407, -82.662),
    "Washington": ("Sandersville", 32.982, -82.810), "Wilkes": ("Washington", 33.737, -82.739),
    "Greene": ("Greensboro", 33.575, -83.182), "Oconee": ("Watkinsville", 33.863, -83.408),
    "Morgan": ("Madison", 33.596, -83.468), "Bleckley": ("Cochran", 32.387, -83.355),
    "Laurens": ("Dublin", 32.540, -82.904), "Johnson": ("Wrightsville", 32.729, -82.720),
}

MEETINGISH = re.compile(
    r"commission|council|board|authority|planning|zoning|hearing|town hall|forum|workshop|"
    r"chamber|development|session|meeting|committee|summit|groundbreaking|ribbon|retreat|"
    r"open house|listening|input", re.I)
FORUM = re.compile(r"town hall|forum|workshop|open house|listening session|input session|"
                   r"community meeting|public hearing", re.I)
MILESTONE = re.compile(r"groundbreaking|ground breaking|ribbon[- ]cutting|grand opening", re.I)
CHAMBER = re.compile(r"chamber|business after hours|networking|luncheon|breakfast", re.I)

PLATFORM = {
    "civicclerk": "CivicClerk portal", "civicplus.calendar": "website calendar",
    "civicplus.agendacenter": "Agenda Center", "civicplus.news": "news",
    "municode": "Municode Meetings portal", "wordpress.events": "website calendar",
    "wordpress.ical": "website calendar", "wordpress.media": "website",
    "wordpress.posts": "news", "html_docs": "website",
}
_GLUED = re.compile(r"\b(St|Street|Rd|Road|Ave|Avenue|Hwy|Highway|Dr|Drive|Pkwy|Parkway|Blvd|Ln|Lane|Way|Ct|Court)(?=[A-Z][a-z])")
_ZIP = re.compile(r",?\s*(?:GA|Georgia)\.?(?:\s*\d{5}(?:-\d{4})?\b|\s*$)")


def _venue(location: str, town: str, county: str) -> str:
    """'535 Telfair StAugusta, GA 30901' -> '535 Telfair St' (town is shown separately)."""
    v = _GLUED.sub(r"\1 ", location or "")
    v = re.sub(rf"(?<=[A-Za-z0-9.]){re.escape(town)}\b", f" {town}", v)       # "...Room 203Eatonton"
    v = _ZIP.sub("", v)
    v = re.sub(rf"(?:,\s*|\s+){re.escape(town)}\b(?:,\s*{re.escape(town)}\b)*\s*$", "", v, flags=re.I)
    v = " ".join(v.split()).strip(" ,")
    return v or f"{county} County government offices"

# Hub scoring buckets (SCORE_PARTS in index.html): leads 40, type 25,
# developer attendance 20, distance 10, cost 5.
TYPE_POINTS = {"Community forum": 23, "Public meeting": 22, "Project milestone": 22,
               "Chamber and networking": 17}


def _event_type(text: str) -> str:
    if MILESTONE.search(text):
        return "Project milestone"
    if FORUM.search(text):
        return "Community forum"
    if CHAMBER.search(text):
        return "Chamber and networking"
    return "Public meeting"


def _core(title: str) -> str:
    """Normalized title for merging the same meeting reported by two feeds."""
    t = re.sub(r"\(.*?\)", " ", title.lower())
    t = re.sub(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\b20\d\d\b", " ", t)
    t = re.sub(r"\b(meeting|agenda|regular|monthly|the|of|county|and|&|-|called|special)\b", " ", t)
    return " ".join(t.split()[:4])


def _clean_title(it: Item, county: str) -> str:
    t = re.sub(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b|\b20\d{6}\b", " ", it.title)   # dates, 20261117
    t = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", t)                                  # "(Board of ...)" suffix
    t = " ".join(t.split()).strip(" -–")
    t = re.sub(r"^(Meeting Agenda|Agenda)\s*[-–]\s*", "", t, flags=re.I)
    t = re.sub(r"(\b[\w ]{4,30}\b)\s+\1", r"\1", t)                               # "Agenda Agenda"
    if re.fullmatch(r"(?i)(meeting )?agenda( meeting)?|meeting", t):
        t = it.meeting_body or "Public Meeting"
    elif it.meeting_body and len(t) < 12:
        t = it.meeting_body
    if county.lower() not in t.lower() and it.county.split("-")[0].lower() not in t.lower():
        t = f"{county} County {t}"
    return t[:140]


def _reason(it: Item) -> str:
    tags = set(it.tags)
    if {"development", "economic_development"} & tags:
        return "Land-use or development items are on the agenda."
    if "community_forum" in tags:
        return "Open to the public, with time for community input."
    if "speaker" in tags:
        return "Includes a presentation or guest speaker."
    if "infrastructure" in tags:
        return "Infrastructure or capital projects are on the agenda."
    return "Public meeting where local decisions and projects come up."


def _agenda(it: Item) -> str:
    if it.highlights:
        return "; ".join(h.rstrip(".") for h in it.highlights[:3])
    nice = {"development": "development and land use", "economic_development": "economic development",
            "infrastructure": "infrastructure", "community_forum": "public comment",
            "speaker": "presentations", "procurement": "bids and contracts"}
    named = [nice[t] for t in it.tags if t in nice]
    return ", ".join(named).capitalize() if named else "Agenda posted on the county site"


def is_hub_event(it: Item, today: date) -> bool:
    if it.event_date is None or it.event_date < today:
        return False
    if it.kind in ("minutes", "news", "document"):
        return False
    if "noise" in it.tags or is_unrelated(it):
        return False
    return bool(MEETINGISH.search(f"{it.title} {it.meeting_body}"))


def build_events(items: list[Item], counties: dict[str, str], today: date) -> list[dict]:
    """counties maps display name ("Macon-Bibb") -> canonical county ("Bibb")."""
    merged: dict[tuple, Item] = {}
    for it in items:
        if not is_hub_event(it, today):
            continue
        canon = counties.get(it.county, it.county)
        key = (canon, it.event_date, _core(it.title) or _core(it.meeting_body))
        cur = merged.get(key)
        if cur is None:
            merged[key] = it
            continue
        # Same meeting from two feeds: keep the richer one, borrow what it lacks.
        best, other = (it, cur) if (it.doc_url and not cur.doc_url) or it.score > cur.score else (cur, it)
        best.event_time = best.event_time or other.event_time
        best.location = best.location or other.location
        best.doc_url = best.doc_url or other.doc_url
        best.highlights = best.highlights or other.highlights
        best.tags = sorted(set(best.tags) | set(other.tags))
        best.score = max(best.score, other.score)
        merged[key] = best

    out = []
    updated = today.strftime("%-d %b")
    for (canon, when, _), it in sorted(merged.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        town, lat, lng = SEATS.get(canon, (canon, 33.0, -83.0))
        etype = _event_type(f"{it.title} {it.meeting_body}")
        dev = max(4, min(20, 4 + it.score * 2))
        out.append({
            "name": _clean_title(it, canon),
            "type": etype,
            "date": when.isoformat(),
            "time": it.event_time or "Time TBD",
            "venue": _venue(it.location, town, canon),
            "town": town,
            "county": canon,
            "ll": [lat, lng],
            "cost": "Free",
            "leads": [],
            "reason": _reason(it),
            "source": f"{canon} County {PLATFORM.get(it.source, 'website')}",
            "updated": updated,
            "parts": [0, TYPE_POINTS[etype], dev, 7, 5],
            "organizer": it.meeting_body or f"{canon} County",
            "agenda": _agenda(it),
            "sponsors": f"{canon} County",
            "speakers": "commissioners, county staff and applicants presenting their projects",
            "url": it.url,
            "docUrl": it.doc_url,
            "tags": [t for t in it.tags if t != "government_meeting"],
            "origin": "county",
        })
    return out


def write_js(events: list[dict], path: Path, generated: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(events, ensure_ascii=False, indent=1)
    path.write_text(
        "// Generated by county-scraper (python -m scraper run). Do not edit by hand.\n"
        f"// {len(events)} upcoming public meetings from county websites, "
        f"generated {generated.isoformat(timespec='minutes')}.\n"
        f"window.COUNTY_EVENTS_GENERATED = {json.dumps(generated.isoformat(timespec='minutes'))};\n"
        f"window.COUNTY_EVENTS = {body};\n"
    )
    return path
