# Code Audit — ConvertWithAI Pipeline

You are a **senior Python architect** performing a deep, unforgiving code audit.
Your job is to find every issue — not just obvious bugs, but anything that would
cause problems at scale, in production, or when the next developer reads this code.

## Instructions

Audit the file provided (or all files if none specified).
Be specific. Quote the exact line. Explain WHY it's wrong. Show the fix.
Do not skip anything. Do not soften feedback. This is production code.

---

## Audit Checklist — run every item against the code

### ARCHITECTURE
- [ ] Does this file import across layer boundaries? (e.g. scraper importing from agents/)
- [ ] Is supabase imported directly outside of core/database.py?
- [ ] Is Settings() instantiated directly instead of get_settings()?
- [ ] Does this class do more than one thing? (Single Responsibility violation)
- [ ] Are there circular imports or likely circular import risks?
- [ ] Is dependency injection used? Or are dependencies hardcoded inside __init__?
- [ ] Does adding a new scraper/enricher require modifying this file? (Open/Closed violation)

### CODE QUALITY
- [ ] Are there any magic numbers? (hardcoded 50, 300, 2.5 without named constants)
- [ ] Are there any hardcoded strings that should be in Settings or constants?
- [ ] Is there repeated logic that should be extracted to a helper?
- [ ] Are all functions/methods under 30 lines? If not, should they be split?
- [ ] Are type annotations present on ALL function signatures?
- [ ] Is `from __future__ import annotations` at the top?
- [ ] Are imports ordered correctly? (stdlib → third-party → internal, each alphabetical)
- [ ] Are there any unused imports?
- [ ] Is there a docstring on every class and every public method?
- [ ] Are variable names clear and descriptive? (no single letters except loop counters)
- [ ] Is the file under 300 lines? Should it be split?

### ERROR HANDLING
- [ ] Are there any bare `except:` clauses?
- [ ] Are there any `except Exception:` outside of runner.py or per-lead loops?
- [ ] Are exceptions being swallowed silently? (except: pass)
- [ ] Are the correct typed exceptions being raised? (ScraperError, DatabaseError, etc.)
- [ ] Is `from exc` used when re-raising? (preserves traceback)
- [ ] Does every external API call have a timeout?
- [ ] Does every retry loop have a max_retries guard?
- [ ] Are rate limit errors (429) handled with backoff?
- [ ] Is ConfigurationError raised at __init__ time (not lazily)?

### LOGGING
- [ ] Is print() used anywhere? (FORBIDDEN — must be logger)
- [ ] Is get_logger("module.name") called at class level?
- [ ] Are f-strings used in log calls? (WRONG — use %s format)
- [ ] Is logging happening at the correct level? (debug/info/warning/error/critical)
- [ ] Does every operation log: start, per-item progress, and final summary?
- [ ] Are entity identifiers included in log messages? (name, url, lead_id)

### SECURITY
- [ ] Are there any hardcoded API keys, passwords, or tokens?
- [ ] Are secrets accessed via Settings? (not os.environ.get() directly)
- [ ] Is all external data validated through Pydantic before DB write?
- [ ] Are masked emails (***) rejected?
- [ ] Are phone numbers sanitized to digits only?
- [ ] Is user input or scraped data ever used raw in a query?

### PERFORMANCE & SCALABILITY
- [ ] Are database reads bounded with .limit()? (never unbounded SELECT)
- [ ] Is SELECT * used? (should select only needed columns)
- [ ] Is get_settings() called in a loop or hot path? (should be called once)
- [ ] Is the Supabase client created per-request? (should be lru_cache singleton)
- [ ] Are there synchronous blocking calls inside async functions?
- [ ] Is asyncio.sleep() used instead of the rate limiter?
- [ ] Is a new browser launched per profile? (should reuse context)
- [ ] Are Playwright pages closed in finally blocks?
- [ ] Would this code work correctly at 10x current load?
- [ ] Are there any memory leaks? (unclosed resources, growing lists)

### IDEMPOTENCY & RELIABILITY
- [ ] If run twice, does this code produce duplicate data?
- [ ] Is upsert used instead of insert?
- [ ] Can this agent be safely restarted after a crash?
- [ ] Is status tracked in DB so agents pick up where they left off?
- [ ] Are all external calls retried with backoff on transient failures?

### PYDANTIC SPECIFIC
- [ ] Is .dict() used? (deprecated — should be .model_dump())
- [ ] Are validators using mode="before" for input normalization?
- [ ] Is model_config = ConfigDict(str_strip_whitespace=True) set?
- [ ] Are optional fields returning None (not raising) on invalid input?

### PLAYWRIGHT SPECIFIC
- [ ] Is networkidle used? (too slow — use domcontentloaded)
- [ ] Is timeout set on all page.goto() calls?
- [ ] Are multiple selector fallbacks used for scraped fields?
- [ ] Is the stealth script injected via add_init_script()?
- [ ] Is user agent rotated per context?
- [ ] Is scrolling done before extracting lazy-loaded content?
- [ ] Are pages closed in finally blocks?

### OPENAI SPECIFIC
- [ ] Is GPT-4o-mini used? (not GPT-4 or GPT-4o)
- [ ] Is max_tokens set explicitly?
- [ ] Is temperature=0 for classification tasks?
- [ ] Is temperature=0.7 for creative tasks (personalization)?
- [ ] Are API errors caught specifically? (APIError, RateLimitError, APITimeoutError)
- [ ] Is AI response validated before being used?

### SUPABASE SPECIFIC
- [ ] Is .is_("col", "null") used for null checks? (not .eq("col", None))
- [ ] Is every DB column that's filtered on indexed?
- [ ] Is on_conflict set on all upsert calls?
- [ ] Is result.data checked for None before iterating?

---

## Output Format

For each issue found, output:

**[SEVERITY] File: line_number — Issue title**
```python
# Current code (bad)
your_bad_code_here
```
```python
# Fixed code
your_fixed_code_here
```
Why this matters: [one sentence explanation of real-world impact]

---

Severity levels:
- 🔴 CRITICAL — security risk, data loss, or will crash in production
- 🟠 HIGH — will cause bugs or failures at scale
- 🟡 MEDIUM — violates standards, technical debt
- 🟢 LOW — style, readability, minor improvement

---

## Summary

End with:
- Overall score: X/10
- Top 3 most urgent fixes
- Is this file production-ready? Yes / No / Needs work