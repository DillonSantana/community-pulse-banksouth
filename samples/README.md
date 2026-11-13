# Samples for the Lead Finder team

These are real scraper outputs from November 13, 2026, covering what `scraper_handoff.md`
section 5 asked for:

| Ask | Where it is |
|---|---|
| A county on AgendaCenter | **Morgan** (`platform: civicplus.agendacenter`) |
| A county on a different platform | **Columbia** (CivicClerk API) and **Burke** (plain web pages) |
| A scanned PDF | `has_text_layer: false`: Morgan's 10-28-2026 BOC Planning Retreat and a Columbia BOC agenda |
| Unusual parcel ID formats | Burke `062A-005` / `062A-005A`; Columbia `Tax Map 072A Parcel 058`, `Tax Map 050 Parcel 002K` |
| A failed run | **Johnson** (`status: failed`, HTTP 403) |

- `sample_scrape.jsonl` holds 4 `scrape_run` records and 32 `document` records. It passes
  `python -m scraper validate`.
- `sample_agenda_items.jsonl` holds 165 `agenda_item` records from `python -m scraper extract`.

Notes:

- **Greene isn't the AgendaCenter example any more.** Greene moved its agendas to
  CivicClerk in late 2025; its AgendaCenter page stops at 2025-11-25.
- **`file_ref` paths point to the machine that ran the scrape.** They look like
  `data/files/<sha256>.pdf`, and the originals aren't committed. `raw_text` holds the
  full text.
- **Everything here is a public county record.** No BankSouth customer data is included.
