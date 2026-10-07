# ADR-0002: GPT-4o-mini with layered grounding checks for personalization

- **Status:** Accepted (already implemented)
- **Date:** 2026-10-07 (records a decision already in the code)
- **Deciders:** Warda Khan
- **Phase:** Build

## Context
Each Day-1 email needs a short opening line about the prospect. An AI model writes it, but the model can invent facts about the business (this happened; see `docs/grounding_pipeline.md`). The project also wants to keep AI spend low (`CLAUDE.md` section 11 requires GPT-4o-mini for pipeline tasks).

Serves ranked attributes: 1. AI accuracy / grounding.

## Decision
We will use GPT-4o-mini to draft the subject and opener, grounded only in the lead's raw scraped description, and pass every draft through five checks before it can reach human review:
1. Model self-reports `grounded = true`.
2. The quoted source snippet appears verbatim in `description_raw`.
3. The company named matches the database record.
4. No industry terms appear that are absent from the description (blocklist scan).
5. A second, independent GPT-4o-mini call checks for contradictions; it fails closed.

One retry on failure; after that the contact is `flagged` and quarantined. Leads with no description get a fixed generic line with no AI call.

## Options considered
| Option | Cost (build / run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A. GPT-4o-mini + 5 checks + human review | Built / low | Done | Low | Accuracy met at low spend | Chosen |
| B. Larger model (GPT-4o) | Higher run cost | — | Still can invent facts | `CLAUDE.md` forbids it for pipeline tasks | Rejected |
| C. No AI, fixed templates only | Lowest | — | No hallucination | Loses personalization | Rejected (kept as fallback) |

## Consequences
- **Positive:** Invented facts are caught by cheap string checks first; the paid check runs only on drafts that already passed.
- **Negative / trade-offs accepted:** Up to 2 attempts × 2 calls per contact; some good drafts get flagged.
- **Follow-ups / revisit triggers:** Revisit if the `flagged` rate is high, or if monthly OpenAI spend grows noticeably.

**Gaps found in the code (2026-10-06):**
- `agents/personalization.py` imports `openai` directly, which `CLAUDE.md` forbids ("wrap in a service class in `core/`").
- Its two OpenAI calls set no `timeout`, which `CLAUDE.md` section 7 requires.
- Tokens and cost are not logged, so AI spend can't be tracked.

## Evidence
- `agents/personalization.py`, `core/content_guard.py`, `tests/test_content_guard.py`, `docs/grounding_pipeline.md` (read 2026-10-06).
- Assumptions still unconfirmed: OpenAI pricing was not re-verified for this ADR.

## Rules for the code (what Claude Code must follow)
- All OpenAI calls go through one service class in `core/`, with a timeout, retries, and logging of model, tokens and latency.
- Never remove or reorder the grounding checks without a new ADR.
- The consistency check must fail closed: an unreadable result counts as a failure.
