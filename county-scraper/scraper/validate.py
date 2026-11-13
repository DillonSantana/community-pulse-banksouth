"""Check a scrape JSONL file against the handoff contract.

Schema checks per line, plus the cross-record rules a schema can't express:
every document points at a scrape_run in the same file, county/FIPS match that
run, and no (source_url, content_hash) pair appears twice.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "contract" / "scraper_contract.schema.json"


def validate_file(path: Path, schema_path: Path | None = None) -> list[str]:
    schema = json.loads((schema_path or DEFAULT_SCHEMA).read_text())
    whole = Draft202012Validator(schema)
    # When the schema defines each record type under $defs, validate against that
    # definition directly so errors name the bad field instead of "not valid under oneOf".
    by_type = {
        t: Draft202012Validator({"$ref": f"#/$defs/{t}", "$defs": schema["$defs"]})
        for t in ("scrape_run", "document") if t in schema.get("$defs", {})
    }
    problems: list[str] = []
    runs: dict[str, tuple[str, str]] = {}
    docs: list[tuple[int, dict]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"line {n}: not JSON ({e})")
            continue
        validator = by_type.get(rec.get("record_type"), whole)
        for err in sorted(validator.iter_errors(rec), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.path) or "(record)"
            problems.append(f"line {n} [{rec.get('record_type')}] {where}: {err.message[:200]}")
        if rec.get("record_type") == "scrape_run":
            runs[rec.get("scrape_run_id")] = (rec.get("county"), rec.get("county_fips"))
        elif rec.get("record_type") == "document":
            docs.append((n, rec))

    for n, rec in docs:
        run = runs.get(rec.get("scrape_run_id"))
        if run is None:
            problems.append(f"line {n}: document references unknown scrape_run_id")
        elif run != (rec.get("county"), rec.get("county_fips")):
            problems.append(f"line {n}: county/FIPS {rec.get('county')}/{rec.get('county_fips')} "
                            f"doesn't match its run {run[0]}/{run[1]}")
        pair = (rec.get("source_url"), rec.get("content_hash"))
        if pair in seen_pairs:
            problems.append(f"line {n}: duplicate source_url + content_hash")
        seen_pairs.add(pair)
    return problems
