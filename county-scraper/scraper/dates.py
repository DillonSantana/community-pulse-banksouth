"""Pull a calendar date out of messy county titles, file names and feed fields."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"

PATTERNS: list[tuple[re.Pattern, str]] = [
    # 2026-11-17 / 2026_11_17
    (re.compile(r"(?<!\d)(20\d\d)[-_./](\d{1,2})[-_./](\d{1,2})(?!\d)"), "ymd"),
    # 11-17-2026, 11/17/2026, 11.17.26, 11-3-2026
    (re.compile(r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](20\d\d|\d\d)(?!\d)"), "mdy"),
    # November 17, 2026 / Nov 17 2026 / Nov. 17th, 2026 / january_13th_2026 (file names)
    (re.compile(r"(?<![a-z])" + _MONTH_RE + r"[\s_-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s_-]+(20\d\d)", re.I), "Mdy"),
    # 17 November 2026
    (re.compile(r"(\d{1,2})[\s_-]+" + _MONTH_RE + r"[\s_-]+(20\d\d)", re.I), "dMy"),
    # CivicPlus AgendaCenter ids: _11122026-888
    (re.compile(r"_(\d{2})(\d{2})(20\d\d)-\d+"), "mdy"),
    # Compact MMDDYYYY in file names, e.g. MeetingMinutesOctober152026 handled by Mdy;
    # plain 11172026 only when bounded by non-digits.
    (re.compile(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(20\d\d)(?!\d)"), "mdy"),
    # NOV52026 / October152026 (month name glued to day+year)
    (re.compile(_MONTH_RE + r"(\d{1,2})(20\d\d)(?!\d)", re.I), "Mdy"),
]


def _build(kind: str, groups: tuple[str, ...]) -> Optional[date]:
    try:
        if kind == "ymd":
            y, m, d = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "mdy":
            m, d, y = int(groups[0]), int(groups[1]), int(groups[2])
            if y < 100:
                y += 2000
        elif kind == "Mdy":
            m, d, y = MONTHS[groups[0][:3].lower()], int(groups[1]), int(groups[2])
        elif kind == "dMy":
            d, m, y = int(groups[0]), MONTHS[groups[1][:3].lower()], int(groups[2])
        else:
            return None
        if not 2000 <= y <= 2100:
            return None
        return date(y, m, d)
    except (ValueError, KeyError):
        return None


def find_date(*texts: str) -> Optional[date]:
    """Return the first plausible date found across the given strings."""
    for text in texts:
        if not text:
            continue
        for rx, kind in PATTERNS:
            for m in rx.finditer(text):
                got = _build(kind, m.groups())
                if got:
                    return got
    return None


try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:                       # no tz database (e.g. Windows without tzdata)
    EASTERN = timezone(timedelta(hours=-5), "EST")

# 2026-11-17T17:00:00Z, 2026-11-17T17:00:00+00:00, 20261117T230000Z (iCal)
_ISO_DT = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})T(\d{2}):?(\d{2})(?::?(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
_OFFSET_END = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def strip_offset(value: str | None) -> str:
    """Drop a trailing 'Z' / '+00:00' so the timestamp is read as local wall time.

    Only for sources known to mislabel local times as UTC (CivicClerk sends a
    5 PM meeting as T17:00:00Z). Everywhere else an offset is real.
    """
    return _OFFSET_END.sub("", (value or "").strip())


def _iso_datetime(text: str) -> Optional[datetime]:
    """Parse the first ISO/iCal timestamp. Offset-aware values become Eastern time;
    values without an offset are taken as the county's local wall time."""
    m = _ISO_DT.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi, s, off = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    except ValueError:
        return None
    if off:
        if off == "Z":
            tz = timezone.utc
        else:
            sign = 1 if off[0] == "+" else -1
            hh, mm = int(off[1:3]), int(off[-2:])
            tz = timezone(sign * timedelta(hours=hh, minutes=mm))
        dt = dt.replace(tzinfo=tz).astimezone(EASTERN).replace(tzinfo=None)
    return dt


def parse_iso(value: str | None) -> Optional[date]:
    """Calendar date of an ISO timestamp, in Eastern time when it carries an offset."""
    if not value:
        return None
    dt = _iso_datetime(value)
    if dt:
        return dt.date()
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return find_date(value)


def parse_rfc822(value: str | None) -> Optional[date]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return find_date(value)


_TIME_12 = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s*m\.?(?![a-z])", re.I)


def _fmt(h: int, m: int) -> str:
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def find_time(*texts: str) -> str:
    """First clock time in the strings, as '6:00 PM'; '' if none.

    ISO/iCal timestamps with an offset ('Z', '+00:00') are converted to Eastern
    time; ones without an offset are the county's local wall time. Eastern
    midnight usually means "no time given" (a date-only event exported as
    T05:00:00Z), so it is returned as ''. Sources that mislabel local time as UTC
    (CivicClerk) must call strip_offset() first.
    """
    for text in texts:
        if not text:
            continue
        m = _TIME_12.search(text)
        if m:
            h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
            return _fmt(h, int(m.group(2) or 0))
        dt = _iso_datetime(text)
        if dt and (dt.hour, dt.minute) != (0, 0):
            return _fmt(dt.hour, dt.minute)
    return ""
