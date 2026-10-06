# Grounding Pipeline — AI Content Safety Flow

**Added:** 2026-08-17
**Covers:** `agents/personalization.py` (Day-1 pitches) and `agents/followup_agent.py` (Day-3/7 follow-ups)
**Why it exists:** two AI-drafted emails once claimed a business was in "real estate" with nothing in its actual data to back that up, and both went out before anyone caught it. This pipeline exists so that failure mode gets caught automatically — by a real check against real scraped text, twice over — instead of by luck.

Automated checks below only ever decide what reaches the human review screen. **Nothing in this flow sends an email by itself** — the only way anything reaches an inbox is through the "Human review → approve" step at the bottom.

---

## Flow

```
Untouched contact (has email, no draft yet)
        │
        ▼
Description scraped? (Yellow Pages category / LinkedIn headline)
        │
   ┌────┴────┐
   no        yes
   │         │
   ▼         ▼
Generic     Generate draft — GPT-4o-mini, fresh isolated call
fallback    (no memory of any other lead or any prior attempt)
(name+city       │
 only, zero       ▼
 GPT call,   Grounding checks — ALL must pass:
 zero risk)    ✓ grounded = true            (AI self-reports it stayed grounded)
   │           ✓ quote verified in source   (its cited snippet must appear
   │                                          verbatim in the real scraped text)
   │           ✓ company matches record     (named company must match the DB)
   │           ✓ no stray industry terms    (blocklist scan vs. the real text)
   │           ✓ 2nd, independent AI check  (separate GPT call, no stake in
   │                                          defending the first draft, asked
   │                                          "does this contradict the
   │                                          description?")
   │                    │
   │              ┌─────┴─────┐
   │            pass         fail
   │              │            │
   │              │      1st failure → retry once (brand-new call, attempt 2)
   │              │      2nd failure → stop, don't retry again
   │              │            │
   │              ▼            ▼
   │      (rejoins below)   FLAGGED — status='flagged', quarantined
   │              │                        │
   └──────────────┤                  ┌─────┴─────┐
                  ▼                retry        discard
         READY FOR REVIEW      (back to top,   (status=
      (status='personalized')   fresh attempt)  'skipped')
                  │
                  ▼
         Human review
    draft shown next to the real scraped description
                  │
            ┌─────┴─────┐
         approve       skip
            │             │
            ▼             ▼
          SENT        NOT SENT
   (the only exit    (skipped by you)
    that reaches
    an inbox)
```

---

## Legend

| Mark | Meaning |
|---|---|
| `Description scraped?` | The only fully-automated branch point — pure code, no AI |
| `Grounding checks` | 5-layer gate; first 4 are plain text/string comparisons (free, instant), the 5th is the only one that costs an API call — deliberately run last |
| `FLAGGED` | Quarantined — visible on the dashboard's `/flagged` screen, never auto-sent, never silently substituted |
| `Human review` | Mandatory for every draft that reaches it, including generic fallbacks — this is the last line of defense, not a formality |

---

## Where the raw text comes from

- **Yellow Pages leads** — the listing's own category tag (e.g. `"Real Estate Agents Business Brokers"`), scraped in `scrapers/yellowpages_scraper.py` via the `.categories` selector.
- **LinkedIn xlsx imports** — the `Headline` column, captured in `pipeline/import_xlsx_leads.py`.
- Stored verbatim as `leads.description_raw` / `leads.description_source` — no interpretation, no categorization, no fixed industry list.

## Order of the 5 checks matters

Checks 1–4 are pure code (string comparisons against `leads.description_raw`) and cost nothing. Check 5 is a real GPT-4o-mini API call. The code runs 1–4 first and only pays for check 5 if a draft has already survived everything free — no point spending an API call on a draft that was already obviously wrong.

See `core/content_guard.py` (the check logic, unit-tested in `tests/test_content_guard.py`) and `agents/personalization.py` (the orchestration).
