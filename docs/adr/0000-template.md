# ADR-NNNN: <Short decision title>

- **Status:** Proposed | Accepted | Superseded by ADR-XXXX | Deprecated
- **Date:** YYYY-MM-DD
- **Deciders:** <name(s)>
- **Phase:** Discovery | Design | Build | Operate

## Context
What problem or force requires a decision? Include the ranked quality attributes this decision serves (e.g., "1. accuracy, 2. cost, 3. latency < 3s p95").

## Decision
What we will do, in one or two sentences, stated as a rule ("We will ...").

## Options considered
| Option | Cost (build / run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A | | | | | Chosen |
| B | | | | | Rejected: <reason> |

## Consequences
- **Positive:**
- **Negative / trade-offs accepted:**
- **Follow-ups / revisit triggers:** e.g., "Revisit if > 10k docs/day or run cost > $X/month"

## Evidence
- Sources (pricing pages, docs, benchmarks), each with the date checked:
- Assumptions still unconfirmed:

## Rules for the code (what Claude Code must follow)
- e.g., "All LLM calls go through `llm_gateway/`; no direct SDK calls elsewhere."
