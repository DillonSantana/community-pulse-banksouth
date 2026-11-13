"""Agenda-item extraction: the step *after* the scraper (scraper_handoff.md section 1).

Reads the contract JSONL (whole documents, untouched) and writes one `agenda_item`
record per land-use / development item it finds, with raw strings for the
Lead Finder to normalize and resolve:

  request_type, parcel_ids_raw, case_numbers_raw, acreage, units, zoning_from/to,
  parties [{name_raw, role}], addresses_raw, outcome, confidence, needs_review

Every item carries char_start/char_end into the document's raw_text so the agent
can cite the exact words. Values are never cleaned beyond whitespace; normalizing
names and parcel IDs is the Lead Finder's job (02_functions.sql).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

EXTRACTOR_VERSION = "0.1.0"
NAMESPACE = uuid.UUID("6f1c2a52-3c1e-4b7e-9a51-1f0e7d0b5a11")

# ---------------------------------------------------------------- segmentation
# An agenda item starts at a line beginning with an enumerator: "14.", "A.", "a)",
# "(3)", "IV.", "Item 7", or Columbia-style "(I1a4)". Offsets index raw_text.
ENUM = re.compile(
    r"^[ \t]*(?P<num>"
    r"\((?:[A-Z]\d+[a-z]?\d*|\d{1,3}|[a-zA-Z]|[ivxIVX]{1,5})\)"   # (I1a4) (3) (a) (iv)
    r"|(?:Item|ITEM)\s+\d{1,3}[.:]?"                                 # Item 7
    r"|(?:\d{1,3}|[A-Z]|[a-z]|[IVX]{1,5})[.)]"                       # 14. A. a) IV.
    r")(?=[ \t]+\S)",
    re.M,
)
SECTION_LINE = re.compile(r"^[ \t]*([A-Z][A-Z &/,'-]{5,60}):?[ \t]*$", re.M)   # "NEW BUSINESS:"

# ---------------------------------------------------------------- classification
REQUEST_TYPES: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("annexation", r"\bannex(ation|ing|ed)?\b"),
    ("rezoning", r"\bre-?zon(e|ing|ed)\b|zoning map amendment|\bfrom\s+[A-Z]{1,4}-?\d{0,2}[A-Z]?\s+to\s+[A-Z]{1,4}-?\d"),
    ("variance", r"\bvariances?\b"),
    ("special_use", r"special (use|exception)|\bSUP\b"),
    ("conditional_use", r"conditional use"),
    ("moratorium", r"\bmoratorium\b"),
    ("plat_preliminary", r"preliminary plat"),
    ("plat_final", r"final plat"),
    ("plat", r"\b(?:re)?plat\s+(?:for|of|approval|amendment)\b|\bplats?\s*[-–—:]|\bminor plat|\bsketch plat"),
    ("subdivision", r"\bsubdivi(sion|de)\b"),
    ("development_agreement", r"development agreement"),
    ("text_amendment", r"text amendment|amend(ment)? (to )?the .{0,40}(zoning|development) (ordinance|regulations)"),
    ("site_plan", r"site plan|development plan|\bPUD\b|planned unit development"),
    ("incentive", r"tax abatement|revenue bonds?|\bPILOT\b|bond (issue|resolution|inducement)|"
                  r"development authority|incentive|enterprise zone"),
]]

# ---------------------------------------------------------------- field patterns
DATE_LIKE = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")
ID_TOKEN = re.compile(r"(?<![\w-])(?=[A-Z0-9.-]*\d)[A-Z0-9]{1,6}(?:[-.][A-Z0-9]{1,6}){1,6}(?![\w-])")
PARCEL_KEY = re.compile(r"\b(?:tax\s+)?(?:parcels?|map\s*(?:&|and|/)\s*parcel|tax\s*map|PIN|"
                        r"tax\s*(?:parcel\s*)?id|parcel\s*(?:no\.?|number|#|id))\b[\s#:.]*", re.I)
MAP_PARCEL = re.compile(r"\b(?:Tax\s+)?Map\s+([A-Z0-9-]+)\s*,?\s*(?:Parcel|Lot)\s+([A-Z0-9-]+)", re.I)
CASE_NO = re.compile(
    r"(?:\b(?:case|application|petition|file|docket)\s*(?:no\.?|number|#)?\s*:?\s*|#\s*)"
    r"([A-Z]{1,5}[- ]?\d{2,4}[-‐]\d{1,5}[A-Z]?)"
    r"|\b((?:RZ|REZ|Z|V|VAR|SUP|CU|CUP|PP|FP|AX|ANX|SP|TA|ZA|P)[- ]?\d{2,4}-\d{1,5})\b", re.I)
ACRES = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:\+/-|±|\+/−|\(\+/-\))?\s*"
                   r"(?:acres?|ac\.)(?![a-z])", re.I)
UNITS = re.compile(r"(\d[\d,]*)\s+(?:residential\s+)?(?:lots|dwellings?|dwelling units|units|homes|"
                   r"homesites|townhomes|apartments)\b", re.I)
ZONE = r"[A-Z]{1,4}(?:-?\d{1,2}[A-Z]?)?"
ZONING_CHANGE = re.compile(
    rf"\bfrom\s+(?:the\s+)?(?:an?\s+)?({ZONE})\b(?:\s*\([^)]{{0,60}}\))?\s*(?:zoning\s+)?(?:district\s+)?"
    rf"(?:zoning\s+)?to\s+(?:the\s+)?(?:an?\s+)?({ZONE})\b")
STREET = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][A-Za-z.'-]*\s+){1,4}"
    r"(?:Road|Rd|Street|St|Highway|Hwy|Parkway|Pkwy|Drive|Dr|Lane|Ln|Avenue|Ave|Boulevard|Blvd|"
    r"Court|Ct|Way|Circle|Cir|Trail|Trl|Place|Pl|Pike|Loop|Run)\b\.?")
LOCATED = re.compile(r"\blocated\s+(?:at|on|along|off)\s+([^.;\n]{4,90})", re.I)

ENTITY_SUFFIX = (r"LLC|L\.L\.C\.|LLLP|LLP|L\.P\.|LP|Inc\.?|Incorporated|Corp\.?|Corporation|Company|"
                 r"Holdings|Properties|Partners|Partnership|Trust|Group|Homes|Development|Investments|"
                 r"Ventures|Realty|Bank|Church|Ministries|Associates|Enterprises|Builders|Construction|"
                 r"Land Company|Communities|Capital|Farms?")
LEGAL_FORM = re.compile(r"\b(LLC|L\.L\.C\.|LLLP|LLP|L\.P\.|LP|Inc\.?|Incorporated|Corp\.?|Corporation|Company)\b")
ENTITY = re.compile(
    rf"((?:(?:[A-Z]\.|[A-Z0-9][A-Za-z0-9&'\-]*)(?:,?\s+|\s*&\s*)){{0,5}}?"
    rf"(?:{ENTITY_SUFFIX})\b\.?(?:,?\s+(?:{ENTITY_SUFFIX})\b\.?)*)")
NOT_A_PARTY = re.compile(r"\b(County|City of|Department|Board|Commission|Planning and Development|"
                         r"Authority|Georgia Power|State of|Ordinance|Regulations|Planned Unit|"
                         r"Copyright|Permission|Consent|Provided|Developer shall)\b", re.I)
GENERIC_LEAD = {"the", "a", "an", "planned", "unit", "residential", "commercial", "new", "this",
                "said", "such", "any", "all", "development", "construction"}


def _plausible_entity(name: str) -> bool:
    words = name.replace(",", " ").split()
    if LEGAL_FORM.search(name):
        # "531 Greene, LLC" is real; "LLC" alone is not.
        return len(words) >= 2
    # Descriptive suffix only ("Homes", "Development"): need a real name in front of it.
    lead = [w for w in words[:-1] if w.lower() not in GENERIC_LEAD]
    return bool(lead) and lead[0][0].isalpha() and not lead[0][0].isdigit()

ROLE_PAREN = re.compile(r"([A-Z][^;()\n]{1,120}?)\s*\((Applicants?|Owners?|Property Owners?|Agents?|"
                        r"Developers?|Petitioners?)\)", re.I)
ROLE_LABEL = re.compile(r"\b(Applicants?|Owners?|Property Owners?|Agents?|Developers?|Petitioners?)\s*"
                        r"(?:\(s\))?\s*:\s*([^;\n]{2,100})", re.I)
ON_BEHALF = re.compile(r"([A-Z][A-Za-z.'\- ]{2,60}?),?\s+(?:as\s+)?(?:on behalf of|agent for|"
                       r"representing|for the owners?,?)\s+(.{3,200}?)(?:,?\s+(?:is|are)\s+request|"
                       r"\s+request|[;\n]|$)", re.I)
REQUESTED_BY = re.compile(r"\b(?:request(?:ed)? by|request(?:s)? (?:from|of)|application (?:of|by)|"
                          r"petition (?:of|by)|submitted by|filed by)\s+([^;\n(]{3,90})", re.I)
SUBJECT_REQUESTS = re.compile(r"^\s*([A-Z][^,;\n]{2,90}?),?\s+(?:is|are)\s+requesting\b", re.M)
AGREEMENT_WITH = re.compile(r"\b(?:agreement|contract|MOU|lease)\s+(?:with|between)\s+([^;\n(]{3,90})", re.I)

STATUS = re.compile(r"\b(WITHDRAWN|TABLED|POSTPONED|DEFERRED|CONTINUED|APPROVED|DENIED|"
                    r"Withdrawn|Tabled|Postponed|Deferred|Approved|Denied)\b")
MOTION = re.compile(r"motion\s+(?:was\s+made\s+)?to\s+(approve|deny|table|postpone|defer)\b"
                    r"(?:(?!motion\s+(?:was\s+made\s+)?to\s).){0,400}?\b(carried|passed|approved|unanimous(?:ly)?|"
                    r"vote(?:d)?\s+\d+\s*-\s*\d+|failed)", re.I | re.S)
OUTCOME_WORD = {"withdrawn": "withdrawn", "tabled": "tabled", "postponed": "tabled", "deferred": "tabled",
                "continued": "tabled", "approved": "approved", "denied": "denied"}


MAX_ITEM_CHARS = 1500          # agenda items are short; longer means we ran into a report
PAGE_MARK = re.compile(r"\bPage\s+\d+\s+of\s+\d+\b", re.I)
HEAD_CHARS = 260               # an item's subject is stated at its start


def _style(num: str) -> str:
    n = num.strip("().:").replace("Item", "").replace("ITEM", "").strip()
    if num.startswith("("):
        return "paren"
    if n.isdigit():
        return "digit"
    if re.fullmatch(r"[IVX]{2,5}|I", n):
        return "roman"
    if re.fullmatch(r"[A-Z]", n):
        return "upper"
    if re.fullmatch(r"[a-z]", n):
        return "lower"
    return "other"


@dataclass
class Segment:
    start: int
    end: int
    number: str
    section: str


def segments(text: str) -> list[Segment]:
    starts = [(m.start(), m.group("num")) for m in ENUM.finditer(text)]
    if not starts:   # notices and news: fall back to paragraphs
        out, pos = [], 0
        for para in re.split(r"\n\s*\n", text):
            if para.strip():
                s = text.find(para, pos)
                out.append(Segment(s, s + len(para), "", ""))
                pos = s + len(para)
        return out
    sections = [(m.start(), m.group(1).strip()) for m in SECTION_LINE.finditer(text)
                if not re.fullmatch(r"(?:A\s?G\s?E\s?N\s?D\s?A|AGENDA)", m.group(1).strip())]
    out = []
    # Short headings by enumerator style, e.g. {"upper": (pos, "Zoning & Subdivision Item")}
    headings: dict[str, tuple[int, str]] = {}
    for i, (s, num) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        e = min(e, s + MAX_ITEM_CHARS)
        page = PAGE_MARK.search(text, s, e)
        if page:
            e = page.start()
        style = _style(num)
        # Section = the nearer of: the last ALL-CAPS heading line, or the last short
        # item of a *different* numbering style (the parent, e.g. "F." above "1.").
        cands = [(p, t) for p, t in sections if p < s][-1:]
        cands += [v for k, v in headings.items() if k != style]
        sec = max(cands)[1] if cands else ""
        body = " ".join(text[s:e].split())
        if len(body) < 70:
            headings[style] = (s, re.sub(r"^\S+\s+", "", body))
        out.append(Segment(s, e, num.strip(), sec))
    return out


# ---------------------------------------------------------------- field extraction
def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        k = x.lower() if isinstance(x, str) else json.dumps(x, sort_keys=True).lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _clean_name(s: str) -> str:
    s = " ".join(s.split()).strip(" ,.;:-–—")
    s = re.sub(r"^(?:and|&|the)\s+", "", s, flags=re.I)
    return s


def _entities(span: str) -> list[str]:
    names = []
    for m in ENTITY.finditer(span):
        n = _clean_name(m.group(1))
        # Drop leading lowercase/glue words the lazy match may pull in.
        n = re.sub(r"^(?:(?:with|of|by|for|from|to|and)\s+)+", "", n, flags=re.I)
        if len(n) > 3 and not NOT_A_PARTY.search(n) and _plausible_entity(n):
            names.append(n)
    return names


def parties(t: str) -> list[dict]:
    out: list[dict] = []

    def add(name: str, role: str):
        name = _clean_name(name)
        if len(name) < 3 or NOT_A_PARTY.search(name):
            return
        if not any(p["name_raw"].lower() == name.lower() for p in out):
            out.append({"name_raw": name, "role": role})

    for m in ON_BEHALF.finditer(t):
        add(m.group(1), "agent")
        owners = _entities(m.group(2)) or [m.group(2)]
        for o in owners:
            add(o, "owner")
    for m in ROLE_PAREN.finditer(t):
        name = re.split(r"\s[-–—]\s|\s[–—]|[–—]\s|\bby\b", m.group(1))[-1]
        if "," in name and not LEGAL_FORM.search(name):
            name = name.split(",")[-1]
        add(name, m.group(2).lower().rstrip("s").replace("property ", "").replace("petitioner", "applicant"))
    for m in ROLE_LABEL.finditer(t):
        add(m.group(2), m.group(1).lower().rstrip("s").replace("property ", "").replace("petitioner", "applicant"))
    for m in REQUESTED_BY.finditer(t):
        # Prefer a business name ("531 Greene, LLC" keeps its comma); else the words
        # up to the first comma or "for/to" clause.
        ents = _entities(m.group(1))
        add(ents[0] if ents else re.split(r",|\s+(?:for|to|on|at)\s", m.group(1))[0], "applicant")
    for m in SUBJECT_REQUESTS.finditer(t):
        if not ON_BEHALF.search(m.group(0)):
            add(m.group(1), "applicant")
    for m in AGREEMENT_WITH.finditer(t):
        for e in _entities(m.group(1)) or ([m.group(1)] if len(m.group(1)) < 50 else []):
            add(e, "counterparty")
    for e in _entities(t)[:5]:      # other named businesses, role unknown
        add(e, "unknown")
    return out


def parcel_ids(t: str) -> list[str]:
    found = [m.group(0) for m in MAP_PARCEL.finditer(t)]
    for m in PARCEL_KEY.finditer(t):
        window = t[m.end(): m.end() + 120]
        got, filler = [], 0
        for tok in re.split(r"\s*(?:,|;|\band\b|&)\s*|\s+", window):
            tok = tok.strip("().:")
            if not tok:
                continue
            is_id = (ID_TOKEN.fullmatch(tok) and not DATE_LIKE.match(tok)
                     and not re.fullmatch(r"\d+\.\d+", tok))      # 31.02 is acreage, not a parcel
            if is_id:
                got.append(tok)
            elif re.fullmatch(r"(?i)parcels?|nos?\.?|numbers?|#|ids?", tok):
                continue
            elif got or filler >= 3:
                break          # the list of IDs ended, or the keyword wasn't followed by one
            else:
                filler += 1    # e.g. "Parcel known as 062A-005"
        found += got
    return _dedupe(found)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def outcome(t: str, doc_type: str) -> tuple[str | None, str | None]:
    m = MOTION.search(t)
    if m:
        verb, result = m.group(1).lower(), m.group(2).lower()
        if result != "failed":
            return {"approve": "approved", "deny": "denied"}.get(verb, "tabled"), " ".join(m.group(0).split())[:300]
    s = STATUS.search(t)
    # On an agenda, only a stated status (e.g. "WITHDRAWN") counts. Never infer.
    if s and (doc_type == "minutes" or s.group(1).isupper()):
        return OUTCOME_WORD[s.group(1).lower()], s.group(1)
    return None, None


def classify(t: str) -> list[str]:
    return [k for k, rx in REQUEST_TYPES if rx.search(t)]


def score_confidence(rec: dict) -> tuple[float, list[str]]:
    conf, reasons = 0.25, []
    if rec["parcel_ids_raw"] or rec["case_numbers_raw"]:
        conf += 0.25
    else:
        reasons.append("no parcel or case number")
    if any(p["role"] in ("applicant", "owner", "agent", "developer", "counterparty") for p in rec["parties"]):
        conf += 0.25
    elif rec["parties"]:
        conf += 0.1
        reasons.append("party role unclear")
    else:
        reasons.append("no applicant or owner found")
    if rec["acreage"] is not None or rec["addresses_raw"]:
        conf += 0.15
    if rec["zoning_from"] or rec["request_type"] not in ("rezoning",):
        conf += 0.1
    else:
        reasons.append("rezoning without from/to districts")
    return round(min(conf, 1.0), 2), reasons


def extract_items(doc: dict) -> list[dict]:
    text = doc.get("raw_text") or ""
    if not text.strip():
        return []
    out = []
    for seg in segments(text):
        raw = text[seg.start:seg.end]
        flat = " ".join(raw.split())
        flat = ENUM.sub("", flat, count=1).strip() if seg.number else flat
        types = classify(flat[:HEAD_CHARS])
        if not types:
            continue
        # A heading like "F. Zoning & Subdivision Item" classifies but isn't an item.
        if len(flat) < 45 and not (ACRES.search(flat) or parcel_ids(flat)):
            continue
        acres = [_num(a) for a in ACRES.findall(flat)]
        units = [int(u.replace(",", "")) for u in UNITS.findall(flat)]
        zc = ZONING_CHANGE.search(flat)
        status, evidence = outcome(flat, doc.get("doc_type", ""))
        rec = {
            "record_type": "agenda_item",
            "agenda_item_id": str(uuid.uuid5(NAMESPACE, f"{doc['source_url']}|{doc['content_hash']}|{seg.start}")),
            "scrape_run_id": doc["scrape_run_id"],
            "county": doc["county"],
            "county_fips": doc["county_fips"],
            "source_url": doc["source_url"],
            "content_hash": doc["content_hash"],
            "doc_type": doc.get("doc_type"),
            "meeting_date": doc.get("meeting_date"),
            "meeting_body_raw": doc.get("meeting_body_raw"),
            "section_raw": seg.section or None,
            "item_number_raw": seg.number or None,
            "char_start": seg.start,
            "char_end": seg.end,
            "item_text": raw.strip(),
            "request_type": types[0],
            "request_types_all": types,
            "parcel_ids_raw": parcel_ids(flat),
            "case_numbers_raw": _dedupe([a or b for a, b in CASE_NO.findall(flat)]),
            "acreage": acres[0] if acres else None,
            "acreage_all": acres,
            "units": units[0] if units else None,
            "zoning_from": zc.group(1) if zc else None,
            "zoning_to": zc.group(2) if zc else None,
            "parties": parties(flat),
            "addresses_raw": _dedupe([" ".join(m.group(0).split()) for m in STREET.finditer(flat)]
                                     + [" ".join(m.group(1).split()) for m in LOCATED.finditer(flat)]),
            "outcome": status,
            "outcome_evidence": evidence,
            "extractor_version": EXTRACTOR_VERSION,
        }
        conf, reasons = score_confidence(rec)
        if doc.get("has_text_layer") is False:
            reasons.append("document has no text layer")
        rec["confidence"] = conf
        rec["needs_review"] = conf < 0.6 or doc.get("has_text_layer") is False
        rec["review_reasons"] = reasons
        out.append(rec)
    return out


def extract_file(src: Path, dst: Path) -> tuple[int, int]:
    n_docs = n_items = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open() as fin, dst.open("w") as fout:
        for line in fin:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("record_type") != "document":
                continue
            n_docs += 1
            for item in extract_items(rec):
                fout.write(json.dumps(item, ensure_ascii=False) + "\n")
                n_items += 1
    return n_docs, n_items
