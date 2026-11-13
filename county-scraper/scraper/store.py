"""SQLite storage (dedupe + 'new since last run') and CSV/JSON/Markdown exports."""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid TEXT PRIMARY KEY,
    county TEXT, region TEXT, kind TEXT, title TEXT, url TEXT, doc_url TEXT, source TEXT,
    event_date TEXT, posted_date TEXT, location TEXT, body TEXT,
    tags TEXT, highlights TEXT, score INTEGER,
    first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS items_county ON items(county);
CREATE TABLE IF NOT EXISTS docs (
    url TEXT PRIMARY KEY,           -- latest version we have of each document URL
    content_hash TEXT, file_type TEXT, file_ref TEXT, raw_text TEXT,
    has_text_layer INTEGER, etag TEXT, last_modified TEXT,
    first_seen TEXT, retrieved_at TEXT
);
CREATE TABLE IF NOT EXISTS doc_versions (
    url TEXT, content_hash TEXT, seen_at TEXT, PRIMARY KEY (url, content_hash)
);
CREATE TABLE IF NOT EXISTS doc_errors (
    url TEXT PRIMARY KEY, error TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    started TEXT, county TEXT, items INTEGER, errors TEXT
);
"""

COLUMNS = ["uid", "county", "region", "kind", "title", "url", "doc_url", "source",
           "event_date", "posted_date", "location", "body", "tags", "highlights", "score"]


class Store:
    """SQLite wrapper. Worker threads fetch documents concurrently, so every call locks."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    # -- documents -------------------------------------------------------
    def doc_state(self, url: str) -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT url, content_hash, file_type, file_ref, raw_text, has_text_layer, "
                "etag, last_modified FROM docs WHERE url=?", (url,)).fetchone()
        if row is None:
            return None
        keys = ["url", "content_hash", "file_type", "file_ref", "raw_text", "has_text_layer",
                "etag", "last_modified"]
        d = dict(zip(keys, row))
        d["has_text_layer"] = None if d["has_text_layer"] is None else bool(d["has_text_layer"])
        return d

    def save_doc(self, doc, etag: str | None, last_modified: str | None) -> None:
        layer = None if doc.has_text_layer is None else int(doc.has_text_layer)
        with self.lock:
            self.db.execute(
                "INSERT INTO docs (url, content_hash, file_type, file_ref, raw_text, has_text_layer, "
                "etag, last_modified, first_seen, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET content_hash=excluded.content_hash, "
                "file_type=excluded.file_type, file_ref=excluded.file_ref, raw_text=excluded.raw_text, "
                "has_text_layer=excluded.has_text_layer, etag=excluded.etag, "
                "last_modified=excluded.last_modified, retrieved_at=excluded.retrieved_at",
                (doc.source_url, doc.content_hash, doc.file_type, doc.file_ref, doc.raw_text, layer,
                 etag, last_modified, doc.retrieved_at, doc.retrieved_at))
            self.db.execute("INSERT OR IGNORE INTO doc_versions VALUES (?,?,?)",
                            (doc.source_url, doc.content_hash, doc.retrieved_at))
            self.db.execute("DELETE FROM doc_errors WHERE url=?", (doc.source_url,))
            self.db.commit()

    def touch_doc(self, url: str, etag: str | None, last_modified: str | None, now: str) -> None:
        with self.lock:
            self.db.execute("UPDATE docs SET etag=COALESCE(?, etag), "
                            "last_modified=COALESCE(?, last_modified), retrieved_at=? WHERE url=?",
                            (etag, last_modified, now, url))
            self.db.commit()

    def doc_error(self, url: str, error: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO doc_errors VALUES (?,?,?)",
                            (url, error[:300], datetime.now().isoformat(timespec="seconds")))
            self.db.commit()

    # -- items -----------------------------------------------------------
    def upsert(self, items: list[Item], now: str) -> set[str]:
        """Insert/update items; return uids that were never seen before."""
        new: set[str] = set()
        with self.lock:
            return self._upsert(items, now, new)

    def _upsert(self, items: list[Item], now: str, new: set[str]) -> set[str]:
        for it in items:
            row = it.to_row()
            exists = self.db.execute("SELECT 1 FROM items WHERE uid=?", (row["uid"],)).fetchone()
            if not exists:
                new.add(row["uid"])
            vals = [row[c] for c in COLUMNS]
            self.db.execute(
                f"INSERT INTO items ({','.join(COLUMNS)}, first_seen, last_seen) "
                f"VALUES ({','.join('?' * len(COLUMNS))}, ?, ?) "
                f"ON CONFLICT(uid) DO UPDATE SET "
                + ", ".join(f"{c}=excluded.{c}" for c in COLUMNS[1:])
                + ", last_seen=excluded.last_seen",
                vals + [now, now],
            )
        self.db.commit()
        return new

    def log_run(self, started: str, county: str, n: int, errors: list[str]) -> None:
        with self.lock:
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?)",
                            (started, county, n, json.dumps(errors)))
            self.db.commit()


def export(items: list[Item], new_uids: set[str], errors: dict[str, list[str]],
           out_dir: Path, min_score: int, run_day: date, suffix: str = "") -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_day.isoformat() + suffix
    ranked = sorted(items, key=lambda i: (-i.score, i.county, str(i.sort_date or "")))

    csv_path = out_dir / f"county_items_{stamp}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS + ["is_new"])
        w.writeheader()
        for it in ranked:
            row = {k: v for k, v in it.to_row().items() if k in COLUMNS}
            row["is_new"] = "yes" if it.uid in new_uids else ""
            w.writerow(row)

    json_path = out_dir / f"county_items_{stamp}.json"
    json_path.write_text(json.dumps(
        [{**it.to_row(), "tags": it.tags, "highlights": it.highlights, "is_new": it.uid in new_uids}
         for it in ranked], indent=2))

    md_path = out_dir / f"digest_{stamp}.md"
    md_path.write_text(_digest(ranked, new_uids, errors, min_score, run_day))
    return {"csv": csv_path, "json": json_path, "digest": md_path}


def _digest(ranked: list[Item], new_uids: set[str], errors: dict[str, list[str]],
            min_score: int, run_day: date) -> str:
    keep = [i for i in ranked if i.score >= min_score]
    by_region: dict[str, dict[str, list[Item]]] = defaultdict(lambda: defaultdict(list))
    for it in keep:
        by_region[it.region][it.county].append(it)

    lines = [f"# County community & development digest - {run_day:%B %-d, %Y}", "",
             f"{len(keep)} items scored {min_score}+ (of {len(ranked)} collected); "
             f"{sum(1 for i in keep if i.uid in new_uids)} are new since the last run.", ""]
    for region in sorted(by_region):
        lines += [f"## {region}", ""]
        for county in sorted(by_region[region]):
            lines.append(f"### {county}")
            for it in sorted(by_region[region][county], key=lambda i: -i.score):
                when = it.sort_date.isoformat() if it.sort_date else "undated"
                new = " **NEW**" if it.uid in new_uids else ""
                tags = ", ".join(t for t in it.tags if t != "government_meeting") or "meeting"
                lines.append(f"- **[{it.title}]({it.url})**{new} - {when} - score {it.score} - _{tags}_")
                if it.doc_url and it.doc_url != it.url:
                    lines.append(f"  - [document]({it.doc_url})")
                for h in it.highlights[:4]:
                    lines.append(f"  - > {h}")
            lines.append("")
    if errors:
        lines += ["## Sources with errors", ""]
        for county, errs in sorted(errors.items()):
            for e in errs:
                lines.append(f"- {county}: {e}")
        lines.append("")
    return "\n".join(lines)
