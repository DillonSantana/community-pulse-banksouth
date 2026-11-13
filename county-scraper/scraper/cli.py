"""Command line entry point.

    python -m scraper run                          # all counties: digest + Lead Finder JSONL
    python -m scraper run --county Morgan --county Greene
    python -m scraper run --no-docs                # quick digest only, no downloads
    python -m scraper extract out/scrape_X.jsonl   # agenda items for the Lead Finder
    python -m scraper validate out/scrape_X.jsonl  # check output against the contract
    python -m scraper housing                      # county housing snapshot (Zillow Research)
    python -m scraper list                         # configured counties
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from .adapters import REGISTRY
from .adapters.base import Context
from .contract import CountyRun, candidate_files, document_record, write_jsonl
from .documents import DocumentFetcher
from .http import Client
from .hub_export import build_events, write_js
from .models import Item
from .store import Store, export
from .tagging import tag_item

ROOT = Path(__file__).resolve().parent.parent
# The Opportunity Hub (index.html) sits one level up; its calendar reads this file.
HUB_EVENTS = ROOT.parent / "data" / "county-events.js"
log = logging.getLogger("scraper")

TAG_TEXT_LIMIT = 60_000      # characters of document text used for scoring/highlights


def load_sources(path: Path) -> list[dict]:
    return json.loads(path.read_text())["counties"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dedupe(items: list[Item]) -> list[Item]:
    """Collapse the same meeting/document reported by two feeds or uploaded twice.

    Two items match on (kind, normalized title, date) - kind keeps a meeting's agenda
    and its minutes apart. On a match, keep the one that carries a document link.
    """
    by_key: dict[tuple, Item] = {}
    order: list[tuple] = []
    for it in items:
        norm = " ".join(it.title.lower().split())
        key = (it.kind, norm, it.sort_date or it.doc_url or it.url)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = it
            order.append(key)
        elif it.doc_url and not cur.doc_url:
            by_key[key] = it
    return [by_key[k] for k in order]


def scrape_county(client: Client, fetcher: DocumentFetcher | None, county: dict,
                  start: date, end: date) -> tuple[list[Item], CountyRun]:
    run = CountyRun(county=county["county"], fips=county["fips"], started_at=now_iso())
    ctx = Context(client=client, county=county["name"], region=county["region"], start=start, end=end)
    items: list[Item] = []
    for cfg in county.get("adapters", []):
        fn = REGISTRY.get(cfg["type"])
        if fn is None:
            ctx.errors.append(f"unknown adapter type {cfg['type']}")
            continue
        items += fn(ctx, cfg)
    items = dedupe(items)
    for it in items:
        tag_item(it)
    run.errors += ctx.errors

    if fetcher is not None:
        doc_errors = 0
        texts: dict[int, list[str]] = {}
        for it, url, kind in candidate_files(items):
            try:
                doc = fetcher.fetch(url)
            except Exception as e:
                doc_errors += 1
                fetcher.store.doc_error(url, f"{type(e).__name__}: {e}")
                log.info("%s document failed %s: %s", county["name"], url, e)
                continue
            run.docs_found += 1
            if doc.raw_text:
                texts.setdefault(id(it), []).append(doc.raw_text)
            if doc.changed:
                run.docs_new += 1
                run.documents.append(document_record(run, it, kind, doc))
        for it in items:
            if id(it) in texts:
                tag_item(it, extra_text="\n".join(texts[id(it)])[:TAG_TEXT_LIMIT])
        if doc_errors:
            run.errors.append(f"{doc_errors} document(s) could not be downloaded")

    if ctx.errors and ctx.parts_ok == 0:
        run.status = "failed"
    elif run.errors:
        run.status = "partial"
    else:
        run.status = "succeeded"
    run.finished_at = now_iso()
    return items, run


def select_counties(args: argparse.Namespace) -> list[dict] | None:
    counties = load_sources(Path(args.sources))
    if not args.county:
        return counties
    wanted = {c.lower() for c in args.county}
    picked = [c for c in counties if c["name"].lower() in wanted or c["county"].lower() in wanted]
    missing = wanted - {c["name"].lower() for c in picked} - {c["county"].lower() for c in picked}
    if missing:
        print(f"Unknown county: {', '.join(sorted(missing))}", file=sys.stderr)
        return None
    return picked


def cmd_run(args: argparse.Namespace) -> int:
    counties = select_counties(args)
    if counties is None:
        return 2
    contact = args.contact or os.environ.get("COUNTY_SCRAPER_CONTACT")
    if not contact:
        print("note: no contact email set (--contact or COUNTY_SCRAPER_CONTACT); "
              "the handoff asks for one in the user agent.", file=sys.stderr)
    today = date.today()
    start, end = today - timedelta(days=args.days_back), today + timedelta(days=args.days_ahead)
    client = Client(respect_robots=not args.ignore_robots, contact=contact)
    store = Store(Path(args.db))
    fetcher = None if args.no_docs else DocumentFetcher(client, store, ROOT / "data" / "files", ROOT)
    started = now_iso()

    all_items: list[Item] = []
    runs: list[CountyRun] = []
    errors: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_county, client, fetcher, c, start, end): c for c in counties}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                items, run = fut.result()
            except Exception as e:      # adapter bug: report it as a failed run, keep going
                items = []
                run = CountyRun(county=c["county"], fips=c["fips"], started_at=started,
                                finished_at=now_iso(), status="failed",
                                errors=[f"scraper crashed: {type(e).__name__}: {e}"])
            all_items += items
            runs.append(run)
            if run.errors:
                errors[c["name"]] = run.errors
            store.log_run(started, c["name"], len(items), run.errors)
            docs = "" if args.no_docs else f"  docs {run.docs_found:3d} ({run.docs_new} new)"
            print(f"  {c['name']:<18} {len(items):4d} items{docs}  {run.status}", flush=True)

    new = store.upsert(all_items, started)
    # A partial run gets its own file names so it never overwrites the full report.
    suffix = "_" + "-".join(sorted(c["name"] for c in counties)).lower() if args.county else ""
    paths = export(all_items, new, errors, Path(args.out), args.min_score, today, suffix)
    stamp = datetime.now().strftime("%Y%m%dT%H%M")
    paths["jsonl"] = write_jsonl(runs, Path(args.out) / f"scrape_{stamp}{suffix}.jsonl")

    # Feed the hub calendar. A --county run would drop every other county from the
    # calendar, so it only writes there when --hub-out is given explicitly.
    hub_path = Path(args.hub_out) if args.hub_out else (
        HUB_EVENTS if not args.county and not args.no_hub and (ROOT.parent / "index.html").exists() else None)
    if hub_path:
        names = {c["name"]: c["county"] for c in counties}
        hub_events = build_events(all_items, names, today)
        paths["hub"] = write_js(hub_events, hub_path, datetime.now().astimezone())
        print(f"{len(hub_events)} upcoming meetings sent to the hub calendar")

    hot = sum(1 for i in all_items if i.score >= args.min_score)
    print(f"\n{len(all_items)} items from {len(counties)} counties "
          f"({len(new)} new, {hot} scored {args.min_score}+), window {start} to {end}")
    if not args.no_docs:
        print(f"{sum(r.docs_found for r in runs)} documents seen, "
              f"{sum(r.docs_new for r in runs)} new or changed")
    for k, p in paths.items():
        print(f"  {k:7} {p}")
    bad = [r for r in runs if r.status != "succeeded"]
    if bad:
        print(f"\n{len(bad)} counties partial/failed (see digest and scrape_run records).")
    return 0


def cmd_housing(args: argparse.Namespace) -> int:
    from .housing import build_snapshot, export_snapshot, write_site_file
    counties = select_counties(args)
    if counties is None:
        return 2
    contact = args.contact or os.environ.get("COUNTY_SCRAPER_CONTACT")
    client = Client(contact=contact)
    snap = build_snapshot(client, counties, Path(args.cache), args.months)
    suffix = "_" + "-".join(sorted(c["name"] for c in counties)).lower() if args.county else ""
    paths = export_snapshot(snap, Path(args.out), date.today(), suffix)
    if args.site:
        if args.county:
            print("--site needs a full run; drop --county", file=sys.stderr)
            return 2
        if not snap["sources"]:
            print("nothing downloaded; the hub's housing file was left as it was", file=sys.stderr)
            return 1
        paths["site"] = write_site_file(snap, Path(args.site))
    for s in snap["sources"]:
        print(f"  {s['label']:<24} through {s['data_through']}  "
              f"{s['counties_covered']:2d}/{len(counties)} counties")
    for e in snap["errors"]:
        print(f"  problem: {e}", file=sys.stderr)
    for k, p in paths.items():
        print(f"  {k:7} {p}")
    return 0 if snap["sources"] else 1


def cmd_extract(args: argparse.Namespace) -> int:
    from .extract import extract_file
    out = Path(args.out) if args.out else Path(args.input).with_name(
        Path(args.input).stem.replace("scrape_", "agenda_items_") + ".jsonl")
    n_docs, n_items = extract_file(Path(args.input), out)
    print(f"{n_items} agenda items from {n_docs} documents -> {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .validate import validate_file
    problems = validate_file(Path(args.input), Path(args.schema) if args.schema else None)
    for p in problems[:50]:
        print(p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def cmd_list(args: argparse.Namespace) -> int:
    for c in load_sources(Path(args.sources)):
        types = ", ".join(a["type"] for a in c.get("adapters", [])) or "(none)"
        print(f"{c['region']:<28} {c['name']:<18} {c['fips']}  {types}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scraper", description="County meeting & development monitor")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--sources", default=str(ROOT / "sources.json"))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scrape counties, download documents, write exports")
    r.add_argument("--county", action="append", help="limit to a county (repeatable)")
    r.add_argument("--days-back", type=int, default=30)
    r.add_argument("--days-ahead", type=int, default=90)
    r.add_argument("--no-docs", action="store_true", help="skip document downloads (digest only)")
    r.add_argument("--contact", help="contact email for the user agent (or COUNTY_SCRAPER_CONTACT)")
    r.add_argument("--min-score", type=int, default=3, help="digest threshold")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--ignore-robots", action="store_true")
    r.add_argument("--db", default=str(ROOT / "data" / "items.db"))
    r.add_argument("--out", default=str(ROOT / "out"))
    r.add_argument("--hub-out", help=f"write hub calendar events here (default {HUB_EVENTS.relative_to(ROOT.parent)} on full runs)")
    r.add_argument("--no-hub", action="store_true", help="don't update the hub calendar")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("extract", help="split scraped documents into structured agenda items")
    e.add_argument("input", help="scrape_*.jsonl from a run")
    e.add_argument("--out", help="output path (default agenda_items_*.jsonl next to input)")
    e.set_defaults(fn=cmd_extract)

    v = sub.add_parser("validate", help="check a scrape JSONL against the handoff contract")
    v.add_argument("input")
    v.add_argument("--schema", help="contract JSON Schema (default contract/scraper_contract.schema.json)")
    v.set_defaults(fn=cmd_validate)

    h = sub.add_parser("housing", help="county housing snapshot from Zillow Research public data")
    h.add_argument("--county", action="append", help="limit to a county (repeatable)")
    h.add_argument("--months", type=int, default=24, help="months of history kept in the JSON")
    h.add_argument("--contact", help="contact email for the user agent (or COUNTY_SCRAPER_CONTACT)")
    h.add_argument("--cache", default=str(ROOT / "data" / "zillow"))
    h.add_argument("--site", nargs="?", const=str(ROOT.parent / "data" / "housing.js"),
                   help="also refresh the hub's Housing page data (default ../data/housing.js)")
    h.add_argument("--out", default=str(ROOT / "out"))
    h.set_defaults(fn=cmd_housing)

    ls = sub.add_parser("list", help="list configured counties")
    ls.set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.fn(args)
