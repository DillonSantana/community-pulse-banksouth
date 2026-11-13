"""Command line entry point.

    python -m scraper run                      # all counties, default window
    python -m scraper run --county Morgan --county Greene --pdf
    python -m scraper list                     # show configured counties
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from . import pdftext
from .adapters import REGISTRY
from .adapters.base import Context
from .http import Client
from .models import Item
from .store import Store, export
from .tagging import tag_item

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("scraper")

PDF_KINDS = {"agenda", "packet", "notice"}


def load_sources(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["counties"]


def scrape_county(client: Client, county: dict, start: date, end: date) -> tuple[list[Item], list[str]]:
    ctx = Context(client=client, county=county["name"], region=county["region"], start=start, end=end)
    items: list[Item] = []
    for cfg in county.get("adapters", []):
        fn = REGISTRY.get(cfg["type"])
        if fn is None:
            ctx.errors.append(f"unknown adapter type {cfg['type']}")
            continue
        items += fn(ctx, cfg)
    return dedupe(items), ctx.errors


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


def enrich_with_pdfs(client: Client, store: Store, items: list[Item], limit: int,
                     download: bool = True) -> int:
    """Re-tag documents using their agenda text.

    Text already in the cache is always used. New PDFs are downloaded only when
    `download` is set, up to `limit` of the highest-scoring candidates.
    """
    candidates = [i for i in items if i.doc_url and i.kind in PDF_KINDS and "noise" not in i.tags]
    candidates.sort(key=lambda i: -i.score)
    done = fetched = 0
    for it in candidates:
        hit, text = store.cached_pdf(it.doc_url)
        if not hit:
            if not download or fetched >= limit:
                continue
            fetched += 1
            try:
                text = pdftext.extract(client, it.doc_url)
                store.save_pdf(it.doc_url, text)
            except Exception as e:
                log.info("pdf skipped %s: %s", it.doc_url, e)
                store.save_pdf(it.doc_url, "", str(e)[:300])
                text = ""
        if text:
            tag_item(it, extra_text=text)
            done += 1
    return done


def cmd_run(args: argparse.Namespace) -> int:
    counties = load_sources(Path(args.sources))
    if args.county:
        wanted = {c.lower() for c in args.county}
        counties = [c for c in counties if c["name"].lower() in wanted]
        missing = wanted - {c["name"].lower() for c in counties}
        if missing:
            print(f"Unknown county: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    today = date.today()
    start, end = today - timedelta(days=args.days_back), today + timedelta(days=args.days_ahead)
    client = Client(respect_robots=not args.ignore_robots)
    store = Store(Path(args.db))
    started = datetime.now().isoformat(timespec="seconds")

    all_items: list[Item] = []
    errors: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_county, client, c, start, end): c["name"] for c in counties}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                items, errs = fut.result()
            except Exception as e:      # adapter bug: report, keep going
                items, errs = [], [f"crashed: {type(e).__name__}: {e}"]
            for it in items:
                tag_item(it)
            all_items += items
            if errs:
                errors[name] = errs
            store.log_run(started, name, len(items), errs)
            status = f"{len(items):4d} items" + (f"  ({len(errs)} error{'s' * (len(errs) > 1)})" if errs else "")
            print(f"  {name:<18} {status}", flush=True)

    n = enrich_with_pdfs(client, store, all_items, args.pdf_limit, download=args.pdf)
    if n:
        print(f"  used agenda text from {n} PDFs" + ("" if args.pdf else " (cached; add --pdf for new ones)"))

    new = store.upsert(all_items, started)
    # A partial run gets its own file names so it never overwrites the full report.
    suffix = "_" + "-".join(sorted(c["name"] for c in counties)).lower() if args.county else ""
    paths = export(all_items, new, errors, Path(args.out), args.min_score, today, suffix)
    hot = sum(1 for i in all_items if i.score >= args.min_score)
    print(f"\n{len(all_items)} items from {len(counties)} counties "
          f"({len(new)} new, {hot} scored {args.min_score}+), window {start} to {end}")
    for k, p in paths.items():
        print(f"  {k:7} {p}")
    if errors:
        print(f"\n{len(errors)} counties had source errors (listed at the end of the digest).")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for c in load_sources(Path(args.sources)):
        types = ", ".join(a["type"] for a in c.get("adapters", [])) or "(none)"
        print(f"{c['region']:<28} {c['name']:<18} {types}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scraper", description="County meeting & development monitor")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--sources", default=str(ROOT / "sources.json"))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scrape counties and write exports")
    r.add_argument("--county", action="append", help="limit to a county (repeatable)")
    r.add_argument("--days-back", type=int, default=30)
    r.add_argument("--days-ahead", type=int, default=90)
    r.add_argument("--pdf", action="store_true", help="download agenda PDFs and tag their contents")
    r.add_argument("--pdf-limit", type=int, default=150, help="max PDFs to read per run")
    r.add_argument("--min-score", type=int, default=3, help="digest threshold")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--ignore-robots", action="store_true")
    r.add_argument("--db", default=str(ROOT / "data" / "items.db"))
    r.add_argument("--out", default=str(ROOT / "out"))
    r.set_defaults(fn=cmd_run)

    ls = sub.add_parser("list", help="list configured counties")
    ls.set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.fn(args)
