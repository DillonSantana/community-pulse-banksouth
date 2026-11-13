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
