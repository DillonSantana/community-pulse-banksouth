# community-pulse-banksouth
AI powered search for community opportunities

**BankSouth Opportunity Hub** — a clickable prototype that helps relationship bankers spot and act on commercial opportunities in their markets.

> All data in this prototype is sample data for demonstration. This repository is public: do not commit real customer, deal, or banker information.

## What's in it

- **Overview** — key stats, priority opportunities table (search and urgency filter), deadlines in the next 30 days, pipeline by market, and team activity. Click a row to open the opportunity detail drawer.
- **Public contract signals** — public-source project and contract records, each with its source and verification date, kept separate from banker-owned opportunities.
- **Where to Be** — events where future clients and project decision-makers are likely to gather, with filters, a relevance score and its breakdown, connected leads, banker coverage, and list / calendar / map views.
- **Campaign briefs** — a guided outreach brief that separates verified facts from hypotheses.

## Run it locally

It's a single self-contained HTML file with no build step or dependencies. Either open `index.html` in a browser, or serve it:

```bash
python3 -m http.server 8080
```

Then open http://localhost:8080.

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
