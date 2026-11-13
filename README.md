# community-pulse-banksouth
AI powered search for community opportunities

**BankSouth Opportunity Hub** — a clickable prototype that helps relationship bankers spot and act on commercial opportunities in their markets.

> Opportunities, customers, leads, contracts and bankers in this prototype are sample data for demonstration. The County signals and Housing market pages show real public records, and the events tagged **County posting** on Where to be are real public meetings taken from county websites. This repository is public: do not commit real customer, deal, or banker information.

## What's in it

- **Overview** — key stats, priority opportunities table (search and urgency filter), deadlines in the next 30 days, pipeline by market, and team activity. Click a row to open the opportunity detail drawer.
- **Public contract signals** — public-source project and contract records, each with its source and verification date, kept separate from banker-owned opportunities.
- **Where to Be** — events where future clients and project decision-makers are likely to gather, with filters, a relevance score and its breakdown, connected leads, banker coverage, and list / calendar / map views. The calendar has a **Month** grid and an at-a-glance **List**, a compact day-by-day table showing time, meeting, county, agenda highlights and relevance. Real upcoming county meetings from `county-scraper` (`data/county-events.js`) appear alongside the sample events, tagged **County posting**.
- **Housing market** — real county home values, list and sale prices, rent, supply and market heat for 30 counties, with two years of history per county. Read from `data/housing.js` (see below).
- **Campaign briefs** — a guided outreach brief that separates verified facts from hypotheses.

## Automation

- **Daily data refresh.** A GitHub Action (`.github/workflows/refresh-county-data.yml`)
  re-scrapes the county websites every morning and commits fresh
  `data/county-events.js` when meetings change. Every successful run also writes the
  time to `data/refresh-status.js`, which the hub shows bottom-left as "Public data
  refreshed … ago". See
  [county-scraper/README.md](county-scraper/README.md#running-it-on-a-schedule).
- **PR checks.** `.github/workflows/pr-checks.yml` runs on every pull request:
  - **Nothing dropped from main** fails if the PR removes a page, nav item, element id
    or function that exists on `main`. This is the usual sign that a merge conflict
    was resolved by keeping only one side.
  - The county scraper tests run when the PR touches `county-scraper/`.

**Resolving conflicts in `index.html`:** most conflicts here are two PRs adding
different things at the same spot. Keep **both** sides. Where both sides edited the
same line (for example the startup call at the bottom), combine the edits by hand.
Then open the page and check every nav item still works.

## Run it locally

It's a single self-contained HTML file with no build step or dependencies. Either open `index.html` in a browser, or serve it:

```bash
python3 -m http.server 8080
```

Then open http://localhost:8080.

## Refreshing the Housing market page

The page reads `data/housing.js`, which the county scraper writes from the files
Zillow Research publishes for download:

```bash
cd county-scraper
```

```bash
.venv/bin/python -m scraper housing --site
```

Then commit the updated `data/housing.js`. Zillow refreshes its files once a month
(around the middle of the month), so a monthly run is enough. The page shows the
month the figures describe and the day they were retrieved. If the file is missing,
the page says so and the rest of the hub works as before.

## County scraper (`county-scraper/`)

A Python scraper that reads public commission and planning postings from 30 Middle Georgia,
East Georgia and BankSouth home-county websites. It produces:

- a ranked digest of development and community-forum items
- the `scrape_run` / `document` JSONL the Lead Finder schema expects
- structured agenda items: request type, parcels, acreage, zoning change, and parties with roles
- a monthly housing snapshot per county (home values, rent, inventory, list and sale
  prices, market heat) from Zillow Research's public data files

See [county-scraper/README.md](county-scraper/README.md). Everything it collects is
public county records; scraped data and downloaded files stay out of git.
