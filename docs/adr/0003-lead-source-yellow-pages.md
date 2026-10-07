# ADR-0003: Lead source — Yellow Pages via curl_cffi

- **Status:** Accepted (decided 2026-05-26 per `docs/workflow.md`)
- **Date:** 2026-10-07 (records the earlier decision)
- **Deciders:** Warda Khan
- **Phase:** Build

## Context
The pipeline targets real estate decision makers (brokers, owners, team leaders). The original plan used Realtor.com, Zillow, Google Maps and Redfin with Playwright.

Per the `docs/workflow.md` changelog (2026-05-26): Realtor.com's bot protection blocked all automation, including real Chrome with stealth. Yellow Pages could be fetched with a single `curl_cffi` request impersonating Chrome, returning full listings without a browser.

Serves ranked attributes: 1. AI accuracy (the listing category gives a real description to ground emails in). Also simpler to run, since no browser is needed.

## Decision
We will source leads from Yellow Pages ("real estate brokers" category) using `scrapers/yellowpages_scraper.py` (curl_cffi, no Playwright). Realtor.com is deprecated; Zillow, Google Maps and Redfin scrapers are dropped. LinkedIn `.xlsx` imports (`pipeline/import_xlsx_leads.py`) are a second, manual source.

## Options considered
| Option | Cost (build / run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A. Yellow Pages via curl_cffi | Low / low | Built | Site changes or blocking | Good | Chosen |
| B. Realtor.com via Playwright | Built / higher (browser) | — | Blocked by bot protection | Not working | Rejected |
| C. Zillow / Google Maps / Redfin | New builds | Weeks | Unknown blocking | Not needed | Dropped |

## Consequences
- **Positive:** No browser, so lower memory use; the category tag is stored as `description_raw` for grounding.
- **Negative / trade-offs accepted:** One source is a single point of failure; listings give the brokerage, not the individual's name.
- **Follow-ups / revisit triggers:** Revisit if Yellow Pages blocks requests or daily verified leads fall below 50.

**Documentation now out of date:**
- `docs/blueprint.md` and `CLAUDE.md` still describe Realtor.com, Playwright and four scrapers.
- `docs/workflow.md` still marks `yellowpages_scraper.py` as "TO BUILD", but the file exists and the orchestrator uses it.

## Evidence
- `docs/workflow.md` changelog, `scrapers/` folder listing, `agents/orchestrator.py` (read 2026-10-06).
- Assumptions still unconfirmed: whether Yellow Pages' terms of use allow automated collection — not checked; Warda to review.

## Rules for the code (what Claude Code must follow)
- New lead sources need a new ADR.
- Do not reintroduce Playwright scraping for Realtor.com.
- Store the raw listing description verbatim in `description_raw`; never categorize or rewrite it.
