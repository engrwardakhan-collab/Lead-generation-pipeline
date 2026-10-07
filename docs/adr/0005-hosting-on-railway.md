# ADR-0005: Hosting the scheduler and webhook on Railway

- **Status:** Proposed (not deployed yet, per `docs/workflow.md`)
- **Date:** 2026-10-07
- **Deciders:** Warda Khan
- **Phase:** Design

## Context
Two processes need to run continuously: the APScheduler orchestrator (`agents/orchestrator.py`, daily pipeline at 06:00 UTC plus a reply check every 30 minutes) and the Flask tracking webhook (`webhook/app.py`). The orchestrator's own docstring says to deploy them as separate Railway services. `docs/blueprint.md` budgets Railway at $0 on a "free tier".

Cost is not a ranked attribute right now (the cost target was removed on 2026-10-07); prices are recorded here for reference.

**What Railway's pricing is today (checked 2026-10-06):** a Free plan at $0 with $1 of usage credit per month (one replica, 0.5 GB RAM, 1 vCPU per service), and Hobby at $5/month, which includes $5 of usage. New accounts start on a trial with a one-time $5 credit.

## Decision
Proposed: Deploy on Railway, starting on the Free plan, and measure actual usage for one month before deciding whether Hobby ($5/month) is needed.

## Options considered
| Option | Cost (run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A. Railway Free, measure first | $0 if usage stays within the $1 credit | Low | Two always-on services may exceed $1/month | Best if it fits | Recommended start |
| B. Railway Hobby | $5/month | Low | Low | Fine if Free isn't enough | Fallback |
| C. Run on own computer | $0 | — | Pipeline stops when the computer sleeps | Not reliable | Rejected |

## Consequences
- **Positive:** Simple deploy, no server management.
- **Negative / trade-offs accepted:** If Free isn't enough, hosting costs $5/month (Hobby) plus OpenAI usage.
- **Follow-ups / revisit triggers:** After one month of real usage, update this ADR with actual costs.

**Risk to check before deploying:** `docs/workflow.md` says SMTP email verification uses port 25 and treats a blocked port as "assume valid". If the host blocks outbound port 25, every address would pass verification and bounces would rise, hurting deliverability. Railway's port-25 policy was not verified for this ADR.

## Evidence
- Railway docs, Pricing and Plans — https://docs.railway.com/pricing and https://docs.railway.com/pricing/plans (checked 2026-10-06).
- `agents/orchestrator.py`, `docs/workflow.md`, `docs/blueprint.md` (read 2026-10-06).
- Assumptions still unconfirmed: actual monthly resource usage of the two services; Railway's outbound port-25 policy.

## Rules for the code (what Claude Code must follow)
- Secrets come only from Railway environment variables, never from committed files.
- If SMTP verification cannot reach port 25, log it as "unverified" instead of treating the address as valid.
