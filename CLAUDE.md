# ConvertWithAI Pipeline — Claude Code Master Standards

> This file is read automatically by Claude Code at the start of every session.
> Every rule here is non-negotiable. Flag violations immediately when found.

---

## PROJECT CONTEXT

- **Product**: Autonomous AI lead generation pipeline for real estate agents
- **Stack**: Python 3.11, Playwright, Supabase, OpenAI GPT-4o-mini, Brevo SMTP, Flask, APScheduler, Telegram
- **Scale Target**: 50-90 verified emails/day (decision makers only — see `docs/blueprint.md`); Brevo free plan caps sending at 300 emails/day; runs 24/7 unattended
- **Cost Constraint**: ~$3/month operational — every token and API call counts

---

## RANKED QUALITY ATTRIBUTES

<!-- Ranking order proposed during architecture review; Warda to confirm or reorder. -->
1. **AI accuracy / grounding** — no AI-drafted email may state facts not in the lead's real scraped data (see `docs/grounding_pipeline.md`)
2. **Run cost** — ~$3/month operational
3. **Email deliverability** — plain text, domain warmup, verified addresses only (see `docs/blueprint.md`)

---

## ARCHITECTURE DECISIONS & WORKFLOW

- Architecture decisions live in `docs/adr/` (template: `docs/adr/0000-template.md`). Follow accepted ADRs. If a change conflicts with an ADR, stop and ask; don't work around it.
- Don't add new infrastructure services, databases or paid dependencies without an ADR.
- Nothing sends an email without human approval on the review screen (see `docs/grounding_pipeline.md`).
- Plan first (plan mode) for any change that touches more than one component.
- After a significant change, ask the `architect-reviewer` subagent to check it against the ADRs.

---

## 1. ARCHITECTURE RULES

### Layer Boundaries — NEVER cross these
```
config/         → read by everyone, imports nothing internal
core/           → imported by scrapers/enrichers/agents, never imports them
scrapers/       → imports core only
enrichers/      → imports core only
agents/         → imports core + scrapers + enrichers
pipeline/       → imports everything, is imported by nothing
webhook/        → standalone Flask app, imports core only
```

### Import Rules
- NEVER import `supabase` directly anywhere except `core/database.py`
- NEVER import `Settings()` directly — always use `get_settings()`
- NEVER import `openai` directly in agents — wrap in a service class in `core/`
- NEVER create circular imports — if you need to, the architecture is wrong
- ALWAYS use `from __future__ import annotations` at top of every file

### Dependency Injection
- ALWAYS inject `db: LeadRepository` into scrapers/enrichers/agents — never instantiate inside
- ALWAYS inject `settings: Settings` as optional param with `get_settings()` as default
- NEVER call `get_settings()` inside a loop or hot path — call once, store as `self._s`

### Single Responsibility
- One class per file (except dataclasses and small helpers)
- One responsibility per class — if you can't describe it in one sentence, split it
- Runner/orchestrator files are the only files allowed to import from multiple layers

---

## 2. CODE QUALITY RULES

### Naming Conventions
```python
# Classes: PascalCase
class RealtorScraper:

# Functions/methods: snake_case
def scrape_profile():

# Constants: SCREAMING_SNAKE_CASE at module level
_BASE_URL = "https://www.realtor.com"
MAX_RETRIES = 3

# Private methods: leading underscore
def _extract_phone():

# Type aliases: PascalCase
LeadDict = dict[str, Any]
```

### Type Annotations — ALWAYS
```python
# Every function must have full type annotations
async def scrape_profile(self, page: Page, url: str) -> Optional[Lead]:
def run(self, max_leads: int = 10) -> EnrichmentResult:

# Use built-in generics (Python 3.10+)
list[str]          # not List[str]
dict[str, Any]     # not Dict[str, Any]
tuple[str, int]    # not Tuple[str, int]
Optional[str]      # still fine from typing
```

### DRY (Don't Repeat Yourself)
- If the same logic appears twice → extract to a helper method
- If the same constant appears twice → define it once at module level
- If the same query appears in two places → add a method to LeadRepository
- If the same prompt appears in two agents → define it in core/prompts.py

### SOLID Principles
- **S** — Each class does ONE thing
- **O** — Add new scrapers by extending BaseScraper, not modifying it
- **L** — Any scraper can replace any other scraper (same interface)
- **I** — BaseEnricher only has methods all enrichers actually need
- **D** — High-level agents depend on abstractions (BaseScraper), not concretions

---

## 3. ERROR HANDLING RULES

### Exception Hierarchy — always use specific types
```python
ConvertAIError          # catch-all, use only in runner
├── ConfigurationError  # missing/invalid env var → EXIT immediately
├── DatabaseError       # Supabase failed → raise, let runner handle
├── ScraperError        # scraping failed → log and CONTINUE to next lead
├── EnrichmentError     # enrichment failed → log and CONTINUE
│   └── RateLimitError  # 429 persisted → STOP enrichment, log, return
```

### Rules Per Layer
```python
# Scrapers: never crash the full run for one bad lead
try:
    lead = await self._scrape_profile(page, url)
except Exception as exc:
    self.logger.warning("Profile failed %s: %s", url, exc)
    result.errors += 1
    continue  # always continue

# Database: always raise DatabaseError (never swallow)
except Exception as exc:
    raise DatabaseError(f"upsert failed: {exc}") from exc

# Config: always raise ConfigurationError at __init__ time
if not self._s.openai_api_key:
    raise ConfigurationError("OPENAI_API_KEY not set")

# Runner: catch specific, never bare Exception at top level
except ConfigurationError as exc:
    logger.error("Config error: %s", exc)
    sys.exit(1)
```

### What to NEVER do
```python
# NEVER swallow exceptions silently
try:
    do_something()
except Exception:
    pass  # ← FORBIDDEN. Always log at minimum.

# NEVER use bare Exception in agent-level code
except Exception as exc:  # ← only allowed in runner + per-lead loops

# NEVER raise generic Exception
raise Exception("something failed")  # ← always use typed exception

# NEVER let a scraper crash the pipeline
# One bad agent profile = skip it, log it, move on
```

### Retry Logic Pattern
```python
# Standard retry pattern for external API calls
MAX_RETRIES = 3
for attempt in range(1, MAX_RETRIES + 1):
    try:
        result = call_external_api()
        break
    except RateLimitError:
        if attempt == MAX_RETRIES:
            raise
        wait = 2 ** attempt * 10  # exponential backoff: 20s, 40s, 80s
        self.logger.warning("Rate limited, retry %d/%d in %ds", attempt, MAX_RETRIES, wait)
        time.sleep(wait)
```

---

## 4. LOGGING RULES

### Setup — always
```python
from core.logging_config import get_logger
logger = get_logger("module.submodule")  # e.g. "scrapers.realtor", "enrichers.website"
```

### What to log at each level
```python
logger.debug(...)    # detailed internals, per-URL progress, DOM extraction
logger.info(...)     # phase starts/ends, lead saved, city done, counts
logger.warning(...)  # single lead failed but pipeline continues, retrying
logger.error(...)    # operation failed, data lost, needs attention
logger.critical(...) # pipeline cannot continue, immediate action needed
```

### Log message format rules
```python
# ALWAYS include: what happened + which entity + key values
logger.info("Lead saved | name=%s | phone=%s | city=%s", lead.name, lead.phone, city)
logger.warning("Profile failed | url=%s | reason=%s", url, exc)
logger.error("DB write failed | lead_id=%s | error=%s", lead_id, exc)

# NEVER use f-strings in log calls (performance — lazy evaluation)
logger.info(f"Saved {name}")        # ← WRONG
logger.info("Saved %s", name)       # ← CORRECT

# NEVER use print() anywhere in the codebase
print("done")                       # ← FORBIDDEN — always use logger
```

### Structured log format
- Every agent logs: START (with key params) → per-item progress → DONE (with totals)
- Every city/batch logs: count of saved / skipped / errors
- Every external API call logs: what was sent, what came back (truncated), status code

---

## 5. SECURITY RULES

### Secrets — absolute rules
```python
# NEVER hardcode any of these:
api_keys, passwords, tokens, urls_with_credentials,
smtp_passwords, webhook_secrets, database_keys

# ALWAYS use Settings / environment variables
self._s.openai_api_key      # ← correct
"sk-proj-abc123"            # ← FORBIDDEN, immediate security violation
```

### Input Validation
- ALWAYS validate external data through Pydantic Lead model before DB write
- NEVER write raw scraped strings directly to Supabase
- ALWAYS sanitize phone numbers (digits only, 10 digits)
- ALWAYS reject masked emails (containing ***)
- NEVER trust external API response structure — always use .get() with defaults

### .env Rules
- NEVER commit .env to git (verify .gitignore has it)
- ALWAYS commit .env.example with placeholder values
- ALWAYS rotate keys immediately if accidentally exposed
- .env.example MUST stay in sync with Settings class fields

---

## 6. PERFORMANCE RULES

### Database
```python
# ALWAYS use upsert, never insert (idempotent operations)
.upsert(data, on_conflict="profile_url")

# ALWAYS select only needed columns (not SELECT *)
.select("id, name, brokerage, location")

# ALWAYS set limits on queries — never unbounded reads
.limit(50)

# ALWAYS use indexes for filtered columns
# leads table needs: idx on status, email, source, scraped_at
```

### Async Code
```python
# NEVER use asyncio.sleep() for rate limiting — use AsyncRateLimiter
await asyncio.sleep(2.5)          # ← use only for one-off waits
await self._rate.wait()           # ← correct for rate limiting

# NEVER mix sync and async incorrectly
# Playwright = async, requests = sync, never call sync in async without executor

# ALWAYS reuse browser context across profiles in same city
# NEVER open a new browser per profile — massive performance hit
```

### Memory
```python
# NEVER load all leads into memory at once
# ALWAYS use .limit() and process in batches of 50
batch_size = 50
leads = self.db.get_unenriched(limit=batch_size)

# ALWAYS close Playwright pages in finally blocks
finally:
    await page.close()
```

### Token Efficiency (OpenAI)
```python
# ALWAYS use GPT-4o-mini, never GPT-4 or GPT-4o for pipeline tasks
model = "gpt-4o-mini"

# ALWAYS set max_tokens explicitly
max_tokens = 50   # for personalization lines (15 words max)
max_tokens = 10   # for classification (single word response)

# ALWAYS write tight prompts — every token costs money
# Target: <100 tokens per prompt for classification tasks
```

---

## 7. SCALABILITY RULES

### Design for 300 leads/day from day 1
- All DB reads MUST be paginated with .limit()
- All external API calls MUST have timeout= set
- All loops MUST have a max iteration guard
- Rate limiters MUST be configurable via Settings, not hardcoded

### Stateless Agents
- Each agent run MUST be restartable from scratch without data corruption
- Upsert, never insert — running twice = same result
- Status field in DB tracks progress — agents pick up where they left off
- No agent should hold state in memory between runs

### Adding New Scrapers/Enrichers
- Create new file in scrapers/ or enrichers/
- Extend BaseScraper or BaseEnricher
- Register in runner.py — no other files need to change
- All new scrapers MUST follow same anti-detection pattern as RealtorScraper

---

## 8. PLAYWRIGHT BEST PRACTICES

```python
# ALWAYS use domcontentloaded not networkidle (faster, less prone to timeout)
await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

# ALWAYS close pages in finally blocks
try:
    page = await context.new_page()
    ...
finally:
    await page.close()

# ALWAYS use multiple selector fallbacks for scraped data
await self._safe_text(page, "selector-1", "selector-2", "fallback-selector")

# NEVER use page.waitForTimeout() — use asyncio.sleep() sparingly
# ALWAYS add stealth script via context.add_init_script()
# ALWAYS rotate user agents per context, not per page
# ALWAYS set viewport, locale, timezone to look human
# ALWAYS scroll before extracting lazy-loaded content
```

---

## 9. PYDANTIC BEST PRACTICES

```python
# ALWAYS use model_config = ConfigDict(str_strip_whitespace=True)
# ALWAYS use field_validator for cleaning, not __init__
# ALWAYS use mode="before" for input normalization
# ALWAYS return None (not raise) for optional fields that fail validation
# NEVER use .dict() — use .model_dump() (Pydantic v2)
# ALWAYS use Field(default=...) not just default= for complex defaults
# ALWAYS use ge=0 for counts, min_length=1 for required strings
```

---

## 10. SUPABASE BEST PRACTICES

```python
# ALWAYS use upsert with on_conflict for idempotency
# ALWAYS wrap every DB call in try/except → raise DatabaseError
# ALWAYS use LeadRepository — never raw supabase client in agents
# NEVER use .execute() without checking response (can fail silently)
# ALWAYS check result.data is not None before iterating
# ALWAYS use .is_("email", "null") not .eq("email", None) for null checks
# Index every column you filter on (status, email, source, scraped_at)
```

---

## 11. OPENAI BEST PRACTICES

```python
# ALWAYS use GPT-4o-mini for all pipeline tasks
# ALWAYS set temperature=0 for classification (deterministic)
# ALWAYS set temperature=0.7 for personalization (creative but controlled)
# ALWAYS set max_tokens explicitly — never let it run unbounded
# ALWAYS wrap in try/except for APIError, RateLimitError, APITimeoutError
# ALWAYS use system prompt + user prompt separation
# NEVER put lead PII in system prompt — only in user message
# ALWAYS validate AI response before using it (could return garbage)
# ALWAYS strip and lowercase classification responses before comparing
```

---

## 12. TESTING RULES

```python
# Every new function needs at least one test
# Use pytest — test files in tests/ mirroring src structure
# tests/test_models.py → tests Lead validation edge cases
# tests/test_database.py → tests repository methods (mock Supabase)
# tests/test_scrapers.py → tests URL extraction logic (mock Playwright)

# ALWAYS test edge cases:
# - Empty strings → should become None
# - Malformed phone → should be rejected
# - Masked email (***) → should be rejected
# - DB timeout → should raise DatabaseError
# - API 429 → should retry then raise RateLimitError
```

---

## 13. GIT & FILE RULES

```
.gitignore MUST contain:
  .env
  __pycache__/
  *.pyc
  .playwright/
  node_modules/
  *.log
  audit_reports/

Commit message format:
  feat: add Zillow scraper
  fix: handle missing phone in profile extraction
  refactor: extract _safe_text to BaseScraper
  chore: update requirements.txt
```

---

## 14. WHEN AUDITING CODE — CHECK ALL OF THE ABOVE PLUS:

- [ ] Are imports ordered: stdlib → third-party → internal?
- [ ] Is there a docstring on every class and public method?
- [ ] Are all magic numbers replaced with named constants?
- [ ] Are all hardcoded strings (URLs, table names) extracted to constants/settings?
- [ ] Is every async function properly awaited?
- [ ] Are context managers used for all resources (browser, files, connections)?
- [ ] Is the Supabase client singleton (lru_cache) — not re-created per call?
- [ ] Are there any synchronous blocking calls inside async functions?
- [ ] Does every new DB column have a corresponding index?
- [ ] Are API timeouts set on every external request?
- [ ] Is there a max_retries guard on every retry loop?
- [ ] Are Pydantic models used for ALL external data (scraped + API responses)?
- [ ] Is logging happening at the right level (not everything at INFO)?
- [ ] Are there any bare `except:` or `except Exception:` outside runner?
- [ ] Are secrets accessed via Settings only — never os.environ directly?
- [ ] Is the file under 300 lines? If not, consider splitting.
- [ ] Does this break if run twice? (idempotency check)
- [ ] Does this work at 10x current load? (scalability check)
