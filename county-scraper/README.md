# County Community & Development Monitor

Scrapes official county websites in **Middle Georgia**, **East Georgia (CSRA)** and
BankSouth's home counties for commission agendas, public hearings, town halls, guest
presentations and development projects (rezonings, annexations, development-authority
deals, data centers, plats, SPLOST work). It ranks what it finds so a banker can read
the top of a digest instead of 30 websites.

## Quick start

```bash
cd community-pulse-banksouth/county-scraper
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export COUNTY_SCRAPER_CONTACT="you@example.com"   # put in the user agent, per the handoff
.venv/bin/python -m scraper run                     # digest + Lead Finder JSONL
.venv/bin/python -m scraper extract out/scrape_<stamp>.jsonl
```

Outputs land in `out/`:

| File | What it is |
|---|---|
| `digest_YYYY-MM-DD.md` | Ranked digest grouped by region → county, with agenda-line highlights and a list of sources that failed |
| `county_items_YYYY-MM-DD.csv` | Every item, sorted by score, with an `is_new` column (Excel-friendly) |
| `county_items_YYYY-MM-DD.json` | Same data for other tools |
| `scrape_YYYYMMDDTHHMM.jsonl` | **Lead Finder handoff**: `scrape_run` + `document` records (see below) |
| `agenda_items_YYYYMMDDTHHMM.jsonl` | Output of `extract`: structured agenda items |

`data/items.db` (SQLite) remembers what's been seen, so `is_new` / **NEW** means
"first time this item appeared". It also keeps every document's hash, ETag and
extracted text. Originals are stored in `data/files/<sha256>.<ext>`.

## Common commands

```bash
.venv/bin/python -m scraper list                                  # configured counties
.venv/bin/python -m scraper run                                   # full run, downloads documents
.venv/bin/python -m scraper run --no-docs                         # quick digest, no downloads
.venv/bin/python -m scraper run --county Greene --county Morgan
.venv/bin/python -m scraper validate out/scrape_<stamp>.jsonl     # check against the contract
.venv/bin/python -m scraper extract  out/scrape_<stamp>.jsonl     # agenda items
.venv/bin/python -m scraper run --days-back 14 --days-ahead 60 --min-score 5
.venv/bin/python -m unittest discover -s tests                    # offline tests
```

A `--county` run writes files with the county names in the file name, so it never
overwrites the full report.

## How it works

Each county in `sources.json` lists one or more **adapters**:

| Adapter | Counties | Source |
|---|---|---|
| `civicplus` | Putnam, Washington, McDuffie, Morgan, Laurens (+ calendar/news for Baldwin, Augusta, Columbia, Jefferson, Greene, Oconee) | AgendaCenter page, Calendar RSS, News Flash RSS |
| `civicclerk` | Macon-Bibb, Columbia, Greene, Oconee | Public CivicClerk API (events + agenda PDFs) |
| `wordpress` | Macon-Bibb, Crawford, Jones, Monroe, Peach, Pulaski, Twiggs, Wilkinson, Taliaferro, Bleckley | WP REST API (posts + uploaded PDFs), JSON-LD events, Tribe iCal |
| `municode` | Augusta-Richmond, Baldwin | Municode Meetings tables (currently blocked, see below) |
| `html_docs` | Houston, Burke, Jenkins, Warren, Hancock, Jefferson | Links on ordinary pages, dates parsed from link text and file names |

Then every item is **tagged and scored** (`scraper/tagging.py`):

- `development`, `economic_development`, `infrastructure`, `community_forum`,
  `speaker`, `procurement`, `government_meeting`. Each one adds weight.
- Strong signals get a bonus: data center, annexation, rezoning, development
  authority, DRI, groundbreaking, town hall, comprehensive plan.
- Routine items are tagged `noise` and pushed down: election results, holiday
  closures, job postings, check registers, cancelled meetings.
- Downloaded agendas, packets and minutes are read, and the matching lines become
  **highlights**, e.g. *"Rezoning Parcels 062A-005 … from R1 to CG"* or *"Presentation
  by Former Mayor Bob Young…"*.

To tune what counts as interesting, edit the `TERMS`, `BONUS` and `NOISE` lists in
`tagging.py`. To add a county, add an entry to `sources.json`. Most Georgia county
sites are CivicPlus or WordPress, so usually only a base URL is needed.

## Hub calendar feed (`../data/county-events.js`)

Every full `run` also refreshes the Opportunity Hub's **Where to be** page (`../index.html`).
It writes upcoming public meetings to `data/county-events.js` at the repo root, as
`window.COUNTY_EVENTS = [...]`. The page loads that file with a `<script>` tag, so it
also works when `index.html` is opened straight from disk.

Which meetings go to the calendar (`scraper/hub_export.py`):

- Dated today or later.
- Not cancelled, and not routine noise or an unrelated board (library, elections,
  recreation programs...).
- Calendar entries must look like a meeting, hearing, forum or project milestone.
- The same meeting reported by two feeds (say, a calendar entry and an agenda PDF) is
  merged. The merged entry keeps the time, venue, agenda link and agenda highlights.

Each event carries:

- the time, a cleaned venue, and the county seat for the map and distance filters
- the agenda highlights pulled from its PDF
- links to the county posting and the agenda document
- `origin: "county"`, which the hub shows as **County posting**

Relevance uses the hub's own score parts: connected leads stay 0 until the Lead
Finder supplies them, and agenda content sets the "developer attendance" part.

A `--county` run doesn't touch the calendar file, since it would drop every other
county. Pass `--hub-out` to write it anyway, or `--no-hub` to skip it on a full run.

## Lead Finder handoff (`scrape_*.jsonl`)

This follows the colleague's `scraper_handoff.md`. The output is JSON Lines with two
record types:

- **`scrape_run`**: one per county per run, *including failures*. Status is
  `succeeded`, `partial` (some sources or documents failed) or `failed`, with
  `error_message` in plain words.
- **`document`**: one per file. `source_url` is the file itself. Each record also has:
  - `content_hash` (sha256 of the raw bytes) and `file_type`
  - `raw_text`, the full text exactly as extracted
  - `file_ref` (stored original) and `has_text_layer` (`false` = scanned, needs OCR)
  - `meeting_body_raw` and `meeting_date`
  - `extra`: title, listing page, platform, ETag/Last-Modified, page count

The rules from the handoff are built in:

- **Canonical county + FIPS.** Each county in `sources.json` has a `county` (e.g.
  `Bibb`, `Richmond`) and a `fips` field, checked against the Census list.
- **Dedupe on (URL, hash).** An unchanged file is not re-sent. A changed file is sent
  again with `extra.previous_content_hash`. Conditional requests (ETag /
  If-Modified-Since) avoid re-downloading files that haven't changed.
- **Agendas, packets *and* minutes.** CivicClerk and Municode attachments are all
  collected.
- **Unrelated boards are skipped.** Library, hospital, elections, assessors,
  pension, recreation and similar are dropped (`contract.UNRELATED_BOARDS`).
  Relevant HTML notices (e.g. a rezoning notice or development news) go out as
  `doc_type: other`.
- **Polite fetching.** Robots.txt and crawl delay are honored, at most one request
  per second per host, public pages only, and a contact email goes in the user agent
  (`--contact` or `COUNTY_SCRAPER_CONTACT`).

`contract/scraper_contract.schema.json` was **rebuilt from the field tables in the
handoff**, because the original schema file wasn't available here. To check against
the colleague's real schema:

```bash
.venv/bin/python -m scraper validate out/scrape_<stamp>.jsonl --schema path/to/scraper_contract.schema.json
```

On top of the schema, the validator checks that each document's county and FIPS
match its run and that no URL+hash pair appears twice.

## Agenda-item extraction (`extract`)

The handoff puts splitting and field extraction *downstream* of the scraper, so the
scraper never modifies documents. `extract` is that downstream step as a separate
command. It reads `scrape_*.jsonl` and writes one `agenda_item` per land-use or
development item:

| Field | Example (real postings) |
|---|---|
| `request_type` (+ `request_types_all`) | `rezoning`, `annexation`, `variance`, `special_use`, `plat_preliminary`, `plat_final`, `subdivision`, `development_agreement`, `moratorium`, `text_amendment`, `site_plan`, `incentive` |
| `parcel_ids_raw` | Burke: `["062A-005", "062A-005A"]` (raw; normalization is `normalize_parcel_id`'s job) |
| `zoning_from` / `zoning_to` | `R1` → `CG` |
| `acreage`, `units` | Greene Lake Society: `317.873`, `559` |
| `parties` `[{name_raw, role}]` | Morgan: Kevin Courchaine `agent`, Burnt Pine Farm, LLC `owner`. Roles: applicant, owner, agent, developer, counterparty, unknown |
| `case_numbers_raw`, `addresses_raw` | `P25-0069`, `1660 Old Epps Bridge Rd` |
| `outcome`, `outcome_evidence` | Set only when the document says so: a minutes motion that carried, or a stated `WITHDRAWN` / `TABLED`. Never inferred |
| `section_raw`, `item_number_raw` | `Zoning & Subdivision Item`, `1.` |
| `char_start` / `char_end`, `item_text` | Exact slice of the document's `raw_text`, for citations |
| `confidence`, `needs_review`, `review_reasons` | Low confidence or a scanned source goes to the data reviewer |

`agenda_item_id` is a stable UUID (derived from URL + hash + offset), so rerunning
extraction doesn't create duplicates. Known limits:

- Packets repeat their agenda, so the same item can appear twice, once from the
  agenda and once from the packet. Link them by parcel and applicant.
- Owner names that appear only inside staff-report narrative or plat drawings
  aren't attributed yet.
- People's names are captured only from role phrases ("X, on behalf of Y",
  "Applicant: X").

## Politeness

- The scraper identifies itself honestly as
  `CountyMeetingMonitor/0.2` (plus your contact email), obeys `robots.txt`, and
  honors each site's `Crawl-delay`.
- It waits at least 1 s between requests to the same host and retries
  temporary errors with a backoff.
- Files are capped at 80 MB. Text extraction stops after 600 pages; this is noted in
  `extra`.
- It does **not** spoof browsers or work around blocks.

## Known gaps (as of 2026-11-13)

| County | Status | Workaround |
|---|---|---|
| Augusta-Richmond, Baldwin (Municode Meetings) | Portal drops non-browser clients | Their CivicPlus calendar and news still come through. For agendas, ask Municode or the county clerk about a feed, or check `augustarichmond-ga.municodemeetings.com` by hand |
| Lincoln | `robots.txt` disallows all crawlers | Check `lincolncountyga.com/boc-meeting-agendas` by hand, or ask the county for permission |
| Glascock, Johnson | Sites return HTTP 403 to automated requests | Check by hand. The CSRA Regional Commission and local papers are fallbacks |
| Wilkes | `wilkescountyga.org` sits behind a Cloudflare bot challenge. (`wilkescounty.net` is Wilkes County, **North Carolina**, so don't use it.) | The Washington-Wilkes Chamber calendar lists commission meetings |
| Hancock, Taliaferro | Little posted online | Low volume; the scraper catches what exists |

Facebook, X and Eventbrite are intentionally out of scope.

## Running it on a schedule

Daily is enough (the handoff notes that P&Z agendas post about a week ahead). With
cron (`crontab -e`):

```
0 6 * * * cd /path/to/community-pulse-banksouth/county-scraper && COUNTY_SCRAPER_CONTACT=you@example.com .venv/bin/python -m scraper run >> data/cron.log 2>&1
```

`county_sources.csv` is the original research map of each county's site and platform.
`sources.json` is what the scraper actually reads.
