"""County housing-market snapshot from Zillow Research's public data files.

Zillow publishes these CSVs for download at https://www.zillow.com/research/data/
(one row per county, one column per month). This module reads the published files
only. It does not touch zillow.com listing or property pages: Zillow's terms forbid
automated access to those, and this project does not work around blocks.

The files are refreshed monthly (around the middle of the month, covering the month
before), so every figure carries the month it describes in `as_of`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .http import Client, FetchError

log = logging.getLogger(__name__)

BASE = "https://files.zillowstatic.com/research/public_csvs/"
ATTRIBUTION = "Source: Zillow Research public data (zillow.com/research/data)"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    path: str
    unit: str      # "usd", "count", "days" or "index"
    note: str


METRICS = [
    Metric("home_value", "Typical home value",
           "zhvi/County_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv", "usd",
           "Zillow Home Value Index: typical value of homes in the middle third, "
           "smoothed and seasonally adjusted. A model estimate, not an appraisal."),
    Metric("rent", "Typical rent",
           "zori/County_zori_uc_sfrcondomfr_sm_month.csv", "usd",
           "Zillow Observed Rent Index: typical asking rent, smoothed."),
    Metric("median_list_price", "Median list price",
           "mlp/County_mlp_uc_sfrcondo_sm_month.csv", "usd", "Smoothed."),
    Metric("median_sale_price", "Median sale price",
           "median_sale_price/County_median_sale_price_uc_sfrcondo_sm_month.csv", "usd",
           "Smoothed. Published about a month behind the other series."),
    Metric("for_sale_inventory", "Homes for sale",
           "invt_fs/County_invt_fs_uc_sfrcondo_sm_month.csv", "count",
           "Active listings at any point in the month, smoothed."),
    Metric("new_listings", "New listings",
           "new_listings/County_new_listings_uc_sfrcondo_sm_month.csv", "count",
           "Listings that came on the market in the month, smoothed."),
    Metric("days_to_pending", "Median days to pending",
           "med_doz_pending/County_med_doz_pending_uc_sfrcondo_sm_month.csv", "days",
           "Median days from listing to pending, smoothed."),
    Metric("market_heat", "Market heat index",
           "market_temp_index/County_market_temp_index_uc_sfrcondo_month.csv", "index",
           "Zillow's balance of buyer demand and supply. Higher favors sellers."),
]

Series = list[tuple[date, float]]


# -- fetching ------------------------------------------------------------
def fetch_metric(client: Client, metric: Metric, cache_dir: Path) -> tuple[str, dict]:
    """Return (csv text, meta). Re-downloads only when Zillow has published a new file.

    If the download fails and an earlier copy exists, that copy is used and
    meta["stale_reason"] says why.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / f"{metric.key}.csv"
    meta_path = cache_dir / f"{metric.key}.meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() and csv_path.exists() else {}
    headers = {}
    if meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    elif meta.get("last_modified"):
        headers["If-Modified-Since"] = meta["last_modified"]
    url = BASE + metric.path
    try:
        resp = client.get(url, headers=headers)
    except FetchError as e:
        if not meta:
            raise
        log.warning("%s: using cached copy (%s)", metric.key, e)
        return csv_path.read_text(), {**meta, "stale_reason": str(e)}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if resp.status_code == 304:
        meta = {**meta, "checked_at": now}
        meta.pop("stale_reason", None)
    else:
        text = resp.content.decode("utf-8-sig")
        if "RegionID" not in text[:200]:
            raise FetchError(f"{url} did not return the expected CSV")
        csv_path.write_text(text)
        meta = {"url": url, "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "retrieved_at": now, "checked_at": now}
    meta_path.write_text(json.dumps(meta, indent=2))
    return csv_path.read_text(), meta


# -- parsing -------------------------------------------------------------
def parse_county_csv(text: str, fips: set[str]) -> dict[str, Series]:
    """Monthly series per county FIPS, for the counties asked for. Blank months are skipped."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    try:
        i_state, i_county = header.index("StateCodeFIPS"), header.index("MunicipalCodeFIPS")
    except ValueError:
        raise ValueError("not a Zillow county file (no FIPS columns)") from None
    months: list[tuple[int, date]] = []
    for i, col in enumerate(header):
        try:
            months.append((i, date.fromisoformat(col)))
        except ValueError:
            continue
    out: dict[str, Series] = {}
    for row in reader:
        if len(row) <= i_county:
            continue
        code = row[i_state].zfill(2) + row[i_county].zfill(3)
        if code not in fips:
            continue
        series: Series = []
        for i, day in months:
            if i < len(row) and row[i] != "":
                try:
                    series.append((day, float(row[i])))
                except ValueError:
                    pass
        if series:
            out[code] = series
    return out


def _pct(now: float, then: float | None) -> float | None:
    if then is None or then == 0:
        return None
    return round((now - then) / then * 100, 1)


def summarize(series: Series, unit: str, history_months: int = 24) -> dict:
    """Latest value with month-over-month and year-over-year change."""
    by_month = {(d.year, d.month): v for d, v in series}
    day, value = series[-1]
    prev = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    digits = 1 if unit in ("days", "index") else 0
    rnd = (lambda v: round(v, digits)) if digits else (lambda v: int(round(v)))
    return {
        "value": rnd(value),
        "as_of": day.strftime("%Y-%m"),
        "mom_pct": _pct(value, by_month.get(prev)),
        "yoy_pct": _pct(value, by_month.get((day.year - 1, day.month))),
        "history": [{"month": d.strftime("%Y-%m"), "value": rnd(v)}
                    for d, v in series[-history_months:]],
    }


def heat_label(index: float) -> str:
    """Zillow's published bands for the market heat index."""
    if index >= 70:
        return "strong seller's market"
    if index >= 55:
        return "seller's market"
    if index >= 44:
        return "neutral"
    if index >= 28:
        return "buyer's market"
    return "strong buyer's market"


# -- snapshot ------------------------------------------------------------
def build_snapshot(client: Client, counties: list[dict], cache_dir: Path,
                   history_months: int = 24) -> dict:
    fips = {c["fips"] for c in counties}
    rows = {c["fips"]: {"name": c["name"], "county": c["county"], "fips": c["fips"],
                        "region": c["region"], "metrics": {}} for c in counties}
    sources, errors = [], []
    for m in METRICS:
        try:
            text, meta = fetch_metric(client, m, cache_dir)
            parsed = parse_county_csv(text, fips)
        except (FetchError, ValueError) as e:
            errors.append(f"{m.label}: {e}")
            continue
        if meta.get("stale_reason"):
            errors.append(f"{m.label}: refresh failed, showing the copy from "
                          f"{meta.get('retrieved_at', 'an earlier run')} ({meta['stale_reason']})")
        for code, series in parsed.items():
            rows[code]["metrics"][m.key] = summarize(series, m.unit, history_months)
        through = max((s[-1][0] for s in parsed.values()), default=None)
        sources.append({"metric": m.key, "label": m.label, "unit": m.unit, "note": m.note,
                        "url": BASE + m.path, "file_last_modified": meta.get("last_modified"),
                        "retrieved_at": meta.get("retrieved_at"),
                        "data_through": through.strftime("%Y-%m") if through else None,
                        "counties_covered": len(parsed)})
    for r in rows.values():
        heat = r["metrics"].get("market_heat")
        if heat:
            heat["label"] = heat_label(heat["value"])
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "sources": sources,
        "errors": errors,
        "counties": sorted(rows.values(), key=lambda r: (r["region"], r["name"])),
    }


# -- exports -------------------------------------------------------------
def _fmt(metric: dict | None, unit: str) -> str:
    if not metric:
        return "-"
    v = metric["value"]
    return f"${v:,.0f}" if unit == "usd" else f"{v:,}"


def _chg(metric: dict | None, key: str = "yoy_pct") -> str:
    if not metric or metric.get(key) is None:
        return "-"
    return f"{metric[key]:+.1f}%"


def export_snapshot(snap: dict, out_dir: Path, run_day: date, suffix: str = "") -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_day.isoformat() + suffix
    json_path = out_dir / f"housing_{stamp}.json"
    json_path.write_text(json.dumps(snap, indent=2))

    csv_path = out_dir / f"housing_{stamp}.csv"
    fields = ["region", "name", "county", "fips"]
    for m in METRICS:
        fields += [m.key, f"{m.key}_as_of", f"{m.key}_mom_pct", f"{m.key}_yoy_pct"]
    fields += ["market_heat_label", "source"]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in snap["counties"]:
            row = {k: c[k] for k in ("region", "name", "county", "fips")}
            for m in METRICS:
                got = c["metrics"].get(m.key)
                if got:
                    row.update({m.key: got["value"], f"{m.key}_as_of": got["as_of"],
                                f"{m.key}_mom_pct": got["mom_pct"], f"{m.key}_yoy_pct": got["yoy_pct"]})
            row["market_heat_label"] = c["metrics"].get("market_heat", {}).get("label", "")
            row["source"] = snap["attribution"]
            w.writerow(row)

    md_path = out_dir / f"housing_{stamp}.md"
    md_path.write_text(_brief(snap, run_day))
    return {"brief": md_path, "csv": csv_path, "json": json_path}


def _brief(snap: dict, run_day: date) -> str:
    through = sorted({s["data_through"] for s in snap["sources"] if s["data_through"]})
    lines = [f"# County housing snapshot - {run_day:%B %-d, %Y}", ""]
    if through:
        lines += [f"Monthly figures through **{through[-1]}**. Zillow refreshes these files once a "
                  "month, so this is the latest published month, not today's listings. "
                  "Changes are year over year. A dash means Zillow does not publish that "
                  "series for the county (usually too few listings).", ""]
    region = None
    for c in snap["counties"]:
        if c["region"] != region:
            region = c["region"]
            lines += [f"## {region}", "",
                      "| County | Home value | YoY | Rent | List price | Sale price | For sale | YoY "
                      "| New listings | Days to pending | Market heat |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        m = c["metrics"]
        heat = m.get("market_heat")
        heat_txt = f"{heat['value']:.0f} ({heat['label']})" if heat else "-"
        lines.append(
            f"| {c['name']} | {_fmt(m.get('home_value'), 'usd')} | {_chg(m.get('home_value'))} "
            f"| {_fmt(m.get('rent'), 'usd')} | {_fmt(m.get('median_list_price'), 'usd')} "
            f"| {_fmt(m.get('median_sale_price'), 'usd')} | {_fmt(m.get('for_sale_inventory'), 'count')} "
            f"| {_chg(m.get('for_sale_inventory'))} | {_fmt(m.get('new_listings'), 'count')} "
            f"| {_fmt(m.get('days_to_pending'), 'days')} | {heat_txt} |")
        nxt = snap["counties"].index(c) + 1
        if nxt == len(snap["counties"]) or snap["counties"][nxt]["region"] != region:
            lines.append("")
    lines += ["## What the columns mean", ""]
    for s in snap["sources"]:
        lines.append(f"- **{s['label']}** (through {s['data_through']}, "
                     f"{s['counties_covered']} counties): {s['note']}")
    lines += ["", f"{snap['attribution']}. Home values are model estimates for a county as a "
              "whole. They are not appraisals and say nothing about a specific property.", ""]
    if snap["errors"]:
        lines += ["## Problems this run", ""] + [f"- {e}" for e in snap["errors"]] + [""]
    return "\n".join(lines)
