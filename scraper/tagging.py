"""Keyword tagging and scoring: what makes an item worth a BankSouth banker's attention.

Edit the TERMS table to tune. Each tag has a weight; strong signals add a bonus.
"""
from __future__ import annotations

import re

from .models import Item

# tag -> (weight, [regexes])
TERMS: dict[str, tuple[int, list[str]]] = {
    "development": (3, [
        r"rezon\w*", r"zoning", r"annex\w*", r"variance", r"subdivision", r"site plan",
        r"\bplat\b", r"land use", r"conditional use", r"special (use|exception)",
        r"develop(ment|er)s?", r"industrial", r"warehouse", r"distribution (center|facility)",
        r"data cent(er|re)", r"manufactur\w*", r"solar (farm|facility|project)",
        r"mixed[- ]use", r"multi-?family", r"apartment\w*", r"townhom\w*", r"housing",
        r"hotel", r"retail", r"commercial", r"\bDRI\b", r"regional impact",
        r"comprehensive plan", r"master plan", r"impact fee",
    ]),
    "economic_development": (3, [
        r"development authority", r"economic development", r"tax (abatement|allocation)",
        r"\bTAD\b", r"\bPILOT\b", r"revenue bond", r"incentive", r"opportunity zone",
        r"groundbreaking", r"ribbon[- ]cutting", r"jobs?\b.*\b(creat|announc)\w*",
        r"\bOneGeorgia\b", r"\bCDBG\b", r"\bARPA\b", r"\bgrants?\b", r"\bEDA\b", r"industrial park",
    ]),
    "infrastructure": (2, [
        r"\bT?SPLOST\b", r"water (and|&) sewer", r"sewer (line|extension|expansion)",
        r"water (line|extension|treatment)", r"broadband", r"road (widening|improvement)",
        r"\bGDOT\b", r"bridge", r"airport", r"capital (project|improvement)",
    ]),
    "procurement": (1, [r"\bRFP\b", r"\bRFQ\b", r"\bbids?\b", r"request for (proposals|qualifications)"]),
    "community_forum": (2, [
        r"town hall", r"public hearing", r"community (meeting|forum|input|conversation)",
        r"\bforum\b", r"workshop", r"open house", r"listening session", r"input session",
        r"public (comment|input)", r"citizen(s)? (input|meeting)", r"roundtable",
    ]),
    "speaker": (2, [
        r"guest speaker", r"keynote", r"presentation (by|from)", r"presenter",
        r"\bspeaker\b", r"\bpanel(ist)?\b", r"luncheon", r"breakfast", r"chamber of commerce",
        r"(will|to) (present|speak|address)",
    ]),
    "government_meeting": (1, [
        r"board of commissioners", r"\bBOC\b", r"commission meeting", r"planning (commission|board)",
        r"work session", r"called meeting", r"regular (meeting|session)", r"council",
        r"board of (zoning|appeals)",
    ]),
}

# Signals that bump an item regardless of tag.
BONUS = [
    (3, r"data cent(er|re)|annexation|rezoning|development authority|\bDRI\b|groundbreaking|"
        r"industrial park|tax abatement|revenue bond|comprehensive plan|town hall"),
]

# Routine/noise items that rarely matter for business development.
NOISE = [
    r"election (summary|results?)|statement of votes|sample ballot|risk[- ]limiting audit",
    r"holiday|closed (for|on|due)|office closure",
    r"now hiring|job (posting|opening)|\bpositions?\b|\bappraiser (i|trainee)\b|employment|seeking .*superintendent",
    r"check register|millage|tax (bill|digest)|property tax",
    r"cancel+ed",
    r"recycl|landfill|burn ban|boil water",
    r"\b(yoga|aerobics|bingo|craft|minecraft|storytime|basketball registration)\b",
]

_COMPILED = {tag: (w, [re.compile(p, re.I) for p in pats]) for tag, (w, pats) in TERMS.items()}
_BONUS = [(w, re.compile(p, re.I)) for w, p in BONUS]
_NOISE = [re.compile(p, re.I) for p in NOISE]
_SENT_SPLIT = re.compile(r"(?<=[.;:])\s+|\n+|\s{3,}")


def score_text(text: str) -> tuple[list[str], int]:
    tags, score = [], 0
    for tag, (weight, pats) in _COMPILED.items():
        hits = sum(1 for p in pats if p.search(text))
        if hits:
            tags.append(tag)
            score += weight + min(hits - 1, 3)   # diminishing returns for many hits
    for w, p in _BONUS:
        if p.search(text):
            score += w
    return tags, score


def highlights(text: str, limit: int = 6) -> list[str]:
    """Sentences/lines from long text (e.g. an agenda PDF) that hit a strong tag."""
    strong = [p for tag in ("development", "economic_development", "infrastructure",
                            "community_forum", "speaker")
              for p in _COMPILED[tag][1]]
    out: list[str] = []
    for chunk in _SENT_SPLIT.split(text):
        chunk = " ".join(chunk.split())
        if 15 <= len(chunk) <= 400 and any(p.search(chunk) for p in strong):
            if chunk not in out:
                out.append(chunk)
            if len(out) >= limit:
                break
    return out


def tag_item(item: Item, extra_text: str = "") -> Item:
    head = f"{item.title} {item.location}"
    full = f"{head} {item.body} {extra_text}"
    tags, score = score_text(full)
    if any(p.search(head) for p in _NOISE):
        tags.append("noise")
        score -= 4
    if extra_text:
        item.highlights = highlights(extra_text)
        score += min(len(item.highlights), 4)
    item.tags, item.score = tags, score
    return item
