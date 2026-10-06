# ConvertWithAI — Pipeline Workflow

**Last updated:** 2026-05-26
**Pipeline status:** All 5 stages complete. Orchestrator built. Scraper pivoted to Yellow Pages (curl_cffi). Next: build yellowpages_scraper.py + Railway deploy.
**Target:** Real estate decision makers only — brokers, owners, team leaders, CEOs. Decision maker targeting achieved by searching Yellow Pages with "real estate broker" category — no post-filter needed.

---

## Complete Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│              DAILY PIPELINE  (6am via Orchestrator)                 │
│              agents/orchestrator.py  ← TO BUILD                     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
╔═════════════════════════════════════════════════════════════════════╗
║  STAGE 1 — SCRAPE                                    ⬜ TO BUILD   ║
║  scrapers/yellowpages_scraper.py                                    ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║  Source:  Yellow Pages (curl_cffi — bypasses Cloudflare via        ║
║           Chrome TLS fingerprint impersonation, no browser needed)  ║
║  Cities:  Miami · Houston · Phoenix · Atlanta · Dallas              ║
║  Search:  category="real estate brokers" per city                   ║
║                                                                     ║
║  WHY Yellow Pages:                                                  ║
║    Realtor.com uses Datadome — blocks all automation incl. real     ║
║    Chrome + stealth. Yellow Pages uses Cloudflare, which curl_cffi  ║
║    bypasses cleanly with a single HTTP request. No Playwright.      ║
║                                                                     ║
║  For each city:                                                     ║
║    Search pages (max_pages per city, ~30 listings/page)             ║
║           │                                                         ║
║           ▼                                                         ║
║    Each listing card extracted:                                     ║
║      brokerage name, phone, website_url, address                    ║
║      (category already = "real estate brokers" → DM guaranteed)    ║
║           │                                                         ║
║           ▼                                                         ║
║    Validate via Pydantic Lead model                                 ║
║    (phone normalised to 10 digits, no phone → skip)                 ║
║           │                                                         ║
║     ┌─────┴─────┐                                                   ║
║  has phone?   no phone                                              ║
║     │              │                                                ║
║     ▼              ▼                                                ║
║  UPSERT to     SKIP lead                                            ║
║  Supabase      (result.skipped++)                                   ║
║  status="new"                                                       ║
║                                                                     ║
║  Dedup key: profile_url = Yellow Pages listing URL (UNIQUE)         ║
║  Source field: "yellowpages"                                        ║
║  Name field: brokerage name (individual agent name not available    ║
║              from listing page — email enricher finds it if needed) ║
╚═════════════════════════════════════════════════════════════════════╝
                                  │
                                  ▼
╔═════════════════════════════════════════════════════════════════════╗
║  STAGE 2 — ENRICH                                    ✅ COMPLETE   ║
║  enrichers/website_enricher.py                                      ║
║  core/smtp_verifier.py                                              ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║  Fetch leads WHERE email IS NULL                                    ║
║  (or high-value: brokerage NOT NULL + listing_count >= min)         ║
║  Filter: only leads that have a personal website_url                ║
║           │                                                         ║
║           ▼                                                         ║
║    Fetch agent homepage (requests, timeout=15s)                     ║
║           │                                                         ║
║     ┌─────┴──────┐                                                  ║
║  fetch        fetch OK                                              ║
║  failed           │                                                 ║
║     │             ▼                                                 ║
║  result       Scan nav links for "contact" / "about"               ║
║  .errors++        │                                                 ║
║           ┌───────┴───────┐                                         ║
║      contact          not found                                     ║
║      page found           │                                         ║
║           │               ▼                                         ║
║           ▼          use homepage text                              ║
║      fetch contact page                                             ║
║           │                                                         ║
║           ▼                                                         ║
║    GPT-4o-mini (temp=0) reads page text                             ║
║    → returns email address or "none"                                ║
║           │                                                         ║
║     ┌─────┴──────┐                                                  ║
║  no email     email found                                           ║
║     │              │                                                ║
║     ▼              ▼                                                ║
║ not_found    SMTP Verification                                      ║
║              DNS MX lookup → RCPT TO on port 25                    ║
║                   │                                                 ║
║          ┌────────┴────────┐                                        ║
║       rejected          accepted                                    ║
║       (550)             (250 or port blocked → assume valid)        ║
║          │                   │                                      ║
║          ▼                   ▼                                      ║
║      not_found      Save email to Supabase                         ║
║                     update_status → "enriched"                     ║
╚═════════════════════════════════════════════════════════════════════╝
                                  │
                                  ▼
╔═════════════════════════════════════════════════════════════════════╗
║  STAGE 3 — PERSONALIZE                               ✅ COMPLETE   ║
║  agents/personalization.py                                          ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║  Fetch leads WHERE status = "enriched"                              ║
║  (ordered by listing_count DESC, nulls last)                        ║
║           │                                                         ║
║           ▼                                                         ║
║    GPT-4o-mini (temp=0.7) reads:                                    ║
║    name + city + brokerage + listing_count                          ║
║           │                                                         ║
║           ▼                                                         ║
║    Generates ONE opening line (max 15 words)                        ║
║    "Saw your 12 listings in Miami Beach this quarter"               ║
║           │                                                         ║
║           ▼                                                         ║
║    update_personalized_line() → status = "personalized"            ║
╚═════════════════════════════════════════════════════════════════════╝
                                  │
                                  ▼
╔═════════════════════════════════════════════════════════════════════╗
║  STAGE 4 — EMAIL                                     ✅ COMPLETE   ║
║  agents/email_agent.py                                              ║
║  core/brevo_sender.py                                               ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║  Fetch leads WHERE status = "personalized"                          ║
║           │                                                         ║
║           ▼                                                         ║
║  DAY 1 ── Send cold intro via Brevo SMTP (port 587, plain text)    ║
║           Subject: "Quick question [First Name]"                    ║
║           Body: personalized_line + template + tracking link       ║
║           update_email_sent() → status = "contacted"               ║
║                                                                     ║
║  DAY 3 ── Send follow-up email                                      ║
║           Subject: "Re: Quick question [First Name]"                ║
║           update last_contacted                                     ║
║                                                                     ║
║  DAY 7 ── Send urgency/final email                                  ║
║           Subject: "Last one [First Name]"                          ║
║           update last_contacted                                     ║
║                                                                     ║
║  Sender: Warda Khan <warda.khan@convertwithai.tech>                 ║
║  Daily limit: 50/day (scale to 300/day over 4 weeks)               ║
╚═════════════════════════════════════════════════════════════════════╝
                                  │
                                  ▼
╔═════════════════════════════════════════════════════════════════════╗
║  STAGE 5 — REPLY                                     ✅ COMPLETE   ║
║  agents/reply_agent.py                                              ║
║  webhook/app.py                                                     ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║  IMAP polls inbox every 30 minutes                                  ║
║           │                                                         ║
║           ▼                                                         ║
║    GPT-4o-mini (temp=0) classifies each reply                       ║
║           │                                                         ║
║    ┌───────────────┬───────────────┐                                ║
║    ▼               ▼               ▼                                ║
║ interested    not_interested   unsubscribe                          ║
║    │               │               │                                ║
║    ▼               ▼               ▼                                ║
║ status=        status=         status=                              ║
║ "interested"   "replied"       "unsubscribed"                       ║
║                                                                     ║
║  No push notification layer — check the inbox directly for replies. ║
║  Outreach sent from warda@convertwithai.tech, forwarded (Porkbun)   ║
║  to convertwithai11@gmail.com, which IMAP polls.                    ║
║                                                                     ║
║  ── PARALLEL TRIGGER ──────────────────────────────────────────    ║
║  Lead clicks tracking link in email                                 ║
║           │                                                         ║
║           ▼                                                         ║
║    Flask webhook (webhook/app.py) receives click event              ║
║           │                                                         ║
║           ▼                                                         ║
║    Fires systeme.io webhook → audit call booked automatically       ║
╚═════════════════════════════════════════════════════════════════════╝
```

---

## Supabase Status Flow

```
Scraper saves lead          Enricher finds email      Personalization agent
       │                            │                          │
       ▼                            ▼                          ▼
    "new"  ──────────────►  "enriched"  ────────────► "personalized"
                                                               │
                                                    Email agent sends Day 1
                                                               │
                                                               ▼
                                                         "contacted"
                                                               │
                                                    Reply agent reads inbox
                                                               │
                                                               ▼
                                                          "replied"
                                                               │
                                              ┌────────────────┤
                                              ▼                ▼
                                        "interested"    "unsubscribed"
```

| Status | Set by | Trigger |
|---|---|---|
| `new` | `Lead` model default | Scraper upserts a lead |
| `enriched` | `update_email()` + `update_status()` | Website enricher finds + verifies email |
| `personalized` | `update_personalized_line()` | Personalization agent writes opening line |
| `contacted` | `update_email_sent()` | Email agent sends Day 1 email |
| `replied` | `update_reply()` | Reply agent classifies as not_interested |
| `interested` | `update_reply()` | Reply agent classifies as interested |
| `unsubscribed` | `update_reply()` | Reply agent classifies as unsubscribe |

---

## File Map — Which File Does What

```
config/
  settings.py          → All env vars in one place (singleton via get_settings())

core/
  models.py            → Pydantic Lead model — validates all scraped data
  database.py          → LeadRepository — every Supabase read/write goes here
  exceptions.py        → Typed exception hierarchy
  logging_config.py    → get_logger() — used everywhere, never print()
  rate_limiter.py      → AsyncRateLimiter (Playwright) + SyncRateLimiter (requests)
  smtp_verifier.py     → DNS MX lookup + SMTP RCPT TO email verification  ✅
  brevo_sender.py      → Brevo SMTP email sending                         ✅

scrapers/
  base.py                  → Abstract BaseScraper
  yellowpages_scraper.py   → Yellow Pages scraper (5 cities, curl_cffi)   ⬜
  realtor_scraper.py       → DEPRECATED — Datadome blocks all automation  ❌

enrichers/
  base.py              → Abstract BaseEnricher
  website_enricher.py  → Website fetch + GPT email extract + SMTP verify  ✅

agents/
  personalization.py   → GPT-4o-mini opens lines (temp=0.7, max 15 words) ✅
  email_agent.py       → 3-email sequence sender via Brevo                ✅
  reply_agent.py       → IMAP inbox monitor + GPT reply classifier        ✅
  orchestrator.py      → Master daily controller (runs all stages at 6am) ✅

pipeline/
  runner.py            → CLI entry point: scrape / enrich / personalize / email / replies / all ✅

webhook/
  app.py               → Flask app, receives tracking link clicks         ✅
```

---

## Build Order (Remaining)

```
1. scrapers/yellowpages_scraper.py   ← NEXT
     curl_cffi + BeautifulSoup, no Playwright
     search "real estate brokers" per city
     extract: brokerage, phone, website, address
     upsert to Supabase, source="yellowpages"

2. Deploy to Railway
     set all .env vars in Railway dashboard
     run python pipeline/runner.py all
```

All 5 pipeline stages (enrich → personalize → email → reply → notify) are already built.
Only the scraper and deployment remain.

---

## Running the Pipeline Now

```bash
# Scrape only
python pipeline/runner.py scrape

# Enrich only (50 leads, uses website + GPT + SMTP)
python pipeline/runner.py enrich

# Enrich only — high-value leads first (brokerage + 5+ listings)
python pipeline/runner.py enrich --high-value --min-listings 5

# Full pipeline: scrape then enrich
python pipeline/runner.py all

# Full pipeline — high-value enrichment only
python pipeline/runner.py all --high-value --min-listings 10
```

---

## Changelog

| Date | Change |
|---|---|
| 2026-05-18 | Initial workflow document created |
| 2026-05-18 | Apollo enricher replaced with website + GPT-4o-mini + SMTP approach |
| 2026-05-18 | `status` field added to Lead model and Supabase schema |
| 2026-05-18 | `update_reply()` now maps classification to correct terminal status |
| 2026-05-18 | All DB methods for future agents added to LeadRepository |
| 2026-05-18 | Target narrowed to decision makers only (brokers, owners, team leaders, CEOs) — solo agents filtered at scrape time via title keyword check |
| 2026-05-18 | `mark_enriched()` added to LeadRepository — atomic single-write replaces two-call update_email + update_status pattern |
| 2026-05-18 | Fixed `_is_platform_url` domain matching (substring → exact), `_extract_email_gpt` match → search, OpenAI APIError/APITimeoutError now caught explicitly |
| 2026-05-20 | Stage 3 complete — `agents/personalization.py` built; `personalize` command added to runner |
| 2026-05-20 | Stage 4 complete — `core/brevo_sender.py` + `agents/email_agent.py` built; `email` command added to runner |
| 2026-05-20 | `update_email_sent` now also sets `last_contacted` atomically; `get_due_day3/day7` + `update_last_contacted` added to LeadRepository |
| 2026-05-20 | Settings: `EMAIL_DAILY_LIMIT` (default 50) + `EMAIL_CTA_URL` added |
| 2026-05-20 | Stage 5 complete — `agents/reply_agent.py` + `core/telegram_bot.py` built; `replies` command added to runner |
| 2026-05-20 | Settings: `IMAP_SERVER/PORT/USERNAME/PASSWORD` added; `get_contacted_by_email()` added to LeadRepository |
| 2026-05-20 | `webhook/app.py` built — `/track?ref=<id>` fires systeme.io webhook + redirects to CTA |
| 2026-05-20 | `agents/orchestrator.py` built — APScheduler daily pipeline at 06:00 UTC + reply check every 30 min |
| 2026-05-20 | Settings: `WEBHOOK_BASE_URL` added; email agent now generates per-lead tracking URLs |
| 2026-05-26 | Scraper pivoted from Realtor.com to Yellow Pages — Datadome blocks all automation on Realtor.com (including real Chrome + stealth); Yellow Pages uses Cloudflare, bypassed cleanly via curl_cffi `impersonate='chrome124'` (single HTTP request, no browser, 200 OK with full listings) |
| 2026-05-26 | Planned zillow/gmaps/redfin scrapers dropped — Yellow Pages alone provides phone + website for all 5 target cities; website enricher handles email discovery |
