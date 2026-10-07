# ADR-0001: Human approval before any outbound email

- **Status:** Accepted (code not yet aligned — see "Follow-ups")
- **Date:** 2026-10-07
- **Deciders:** Warda Khan
- **Phase:** Build

## Context
`docs/grounding_pipeline.md` (added 2026-08-17) states that nothing in the AI flow sends an email by itself; the only path to an inbox is "Human review → approve". It exists because two AI-drafted emails once claimed a business was in "real estate" with nothing in its data to support that, and both went out before anyone caught it.

Serves ranked attributes: 1. AI accuracy / grounding, 2. Email deliverability (wrong or irrelevant emails get marked as spam).

**What the code does today (checked 2026-10-06):**
- `agents/orchestrator.py` → `daily_pipeline()` runs `EmailAgent.run()` every day at 06:00 UTC.
- `EmailAgent.run()` sends Day-1 emails to every contact with `status='personalized'` (via `ContactRepository.get_personalized_unsent`), then Day-3 and Day-7 follow-ups, with no approval check.
- `EmailAgent.send_one()` is the dashboard review path, where a human approves each email.
- So the scheduled job bypasses the review screen. `CLAUDE.md` also described the pipeline as "runs 24/7 unattended" (updated 2026-10-07).

## Decision
**We will send an email only after a human approves it on the review screen. This applies to every email in the sequence: Day 1, Day 3 and Day 7.** The scheduled job may prepare drafts but never sends.

## Options considered
| Option | Cost (build / run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A. Approval for every email (Day 1, 3, 7) | Small change: remove the `EMAIL` stage from the scheduler or gate it on an `approved` status | Low | Lowest | Best for accuracy | Chosen by Warda (2026-10-07) |
| B. Approval for Day-1 only (AI-written); Day-3/7 are fixed templates, auto-sent | Small change | Low | Medium: follow-ups go out unattended | Good: Day-3/7 contain no AI text | Rejected by Warda |
| C. Keep current fully automatic sending | None | — | High: repeats the incident in grounding_pipeline.md | Contradicts priority 1 | Rejected |

## Consequences
- **Positive:** No AI-drafted email reaches a prospect without a human check.
- **Negative / trade-offs accepted:** Daily review time; volume is limited by how many drafts Warda can review (fits the 50–90/day target).
- **Follow-ups / revisit triggers:**
  - **Required code change (not done yet):** stop `agents/orchestrator.py` from calling `EmailAgent.run()`, or gate it on an explicit approval status, so the scheduled job can no longer send. Day-3 and Day-7 follow-ups also need to appear on the review screen.
  - Revisit if grounding failures (`flagged` rate) stay near zero for an extended period and review becomes the bottleneck.

## Evidence
- `docs/grounding_pipeline.md`, `agents/orchestrator.py`, `agents/email_agent.py`, `core/database.py` (read 2026-10-06).
- Assumptions still unconfirmed: whether the orchestrator is currently deployed or running anywhere (`docs/workflow.md` lists Railway deploy as not done).

## Rules for the code (what Claude Code must follow)
- No scheduled job, script or CLI command may send an email; sending happens only through the approval path (`EmailAgent.send_one()` from the review dashboard).
- Any new send path must check for an explicit human approval first.
