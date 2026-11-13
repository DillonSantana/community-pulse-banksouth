# County Community & Development Monitor

Scrapes official county websites in **Middle Georgia**, **East Georgia (CSRA)** and
BankSouth's home counties for commission agendas, public hearings, town halls, guest
presentations and development projects (rezonings, annexations, development-authority
deals, data centers, plats, SPLOST work). It ranks what it finds so a banker can read
the top of a digest instead of 30 websites.

## Quick start

```bash
cd ~/Downloads/banksouth-scraper
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scraper run --pdf
```

Outputs land in `out/`:

| File | What it is |
|---|---|
| `digest_YYYY-MM-DD.md` | Ranked digest grouped by region → county, with agenda-line highlights and a list of sources that failed |
| `county_items_YYYY-MM-DD.csv` | Every item, sorted by score, with an `is_new` column (Excel-friendly) |
| `county_items_YYYY-MM-DD.json` | Same data for other tools |

`data/items.db` (SQLite) remembers what's been seen, so `is_new` / **NEW** means
"first time this item appeared", and caches PDF text so agendas are downloaded once.

## Common commands

```bash
.venv/bin/python -m scraper list                                  # configured counties
.venv/bin/python -m scraper run                                   # fast pass, uses cached PDF text
.venv/bin/python -m scraper run --pdf                             # also read new agenda PDFs (~8 min)
.venv/bin/python -m scraper run --county Greene --county Morgan --pdf
.venv/bin/python -m scraper run --days-back 14 --days-ahead 60 --min-score 5
.venv/bin/python -m unittest discover -s tests                    # offline tests
```

A `--county` run writes files with the county names in the file name, so it never
overwrites the full report.

## How it works

Each county in `sources.json` lists one or more **adapters**:

| Adapter | Counties | Source |
|---|---|---|
| `civicplus` | Putnam, Washington, McDuffie, Wilkes, Morgan, Laurens (+ calendar/news for Baldwin, Augusta, Columbia, Jefferson, Greene, Oconee) | AgendaCenter page, Calendar RSS, News Flash RSS |
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
- With `--pdf`, agenda and notice PDFs are read, and the matching lines become
  **highlights**, e.g. *"Rezoning Parcels 062A-005 … from R1 to CG"* or *"Presentation
  by Former Mayor Bob Young…"*.

To tune what counts as interesting, edit the `TERMS`, `BONUS` and `NOISE` lists in
`tagging.py`. To add a county, add an entry to `sources.json`. Most Georgia county
sites are CivicPlus or WordPress, so usually only a base URL is needed.

## Politeness

- The scraper identifies itself honestly as
  `CountyMeetingMonitor/0.1`, obeys `robots.txt`, and honors each site's
  `Crawl-delay`.
- It waits at least 1 s between requests to the same host and retries
  temporary errors with a backoff.
- PDFs are capped at 25 MB and 30 pages, and their text is cached.
- It does **not** spoof browsers or work around blocks.

## Known gaps (as of 2026-11-13)

| County | Status | Workaround |
|---|---|---|
| Augusta-Richmond, Baldwin (Municode Meetings) | Portal drops non-browser clients | Their CivicPlus calendar and news still come through. For agendas, ask Municode or the county clerk about a feed, or check `augustarichmond-ga.municodemeetings.com` by hand |
| Lincoln | `robots.txt` disallows all crawlers | Check `lincolncountyga.com/boc-meeting-agendas` by hand, or ask the county for permission |
| Glascock, Johnson | Sites return HTTP 403 to automated requests | Check by hand. The CSRA Regional Commission and local papers are fallbacks |
| Hancock, Taliaferro | Little posted online | Low volume; the scraper catches what exists |

Facebook, X and Eventbrite are intentionally out of scope.

## Running it on a schedule

Weekly on Monday at 7am with cron (`crontab -e`):

```
0 7 * * 1 cd $HOME/Downloads/banksouth-scraper && .venv/bin/python -m scraper run --pdf >> data/cron.log 2>&1
```

`county_sources.csv` is the original research map of each county's site and platform.
`sources.json` is what the scraper actually reads.
