# ADR-0004: Email sending via Brevo SMTP, and CAN-SPAM compliance

- **Status:** Proposed — compliance decision deferred by Warda (2026-10-07); revisit before live sending
- **Date:** 2026-10-07
- **Deciders:** Warda Khan
- **Phase:** Build

## Context
The pipeline sends a 3-email sequence (Day 1, 3, 7) in plain text through Brevo SMTP from a separate cold-email domain (`convertwithai.tech`, per `.env.example` and `docs/blueprint.md`). The default daily limit is 50 (`EMAIL_DAILY_LIMIT`, per the `docs/workflow.md` changelog), rising toward the volume target of 50–90 verified emails/day.

Serves ranked attributes: 2. Email deliverability.

**Compliance gap found (2026-10-06):** the templates in `agents/email_agent.py` contain no physical postal address and no opt-out instructions. The FTC's CAN-SPAM guide requires both in commercial email: a valid physical postal address (a street address, a registered USPS PO box, or a registered private mailbox), and a clear explanation of how to opt out, honored within 10 business days. CAN-SPAM applies to B2B email too. (I'm not a lawyer; confirm with the FTC guide.)

## Decision
Proposed: We will keep Brevo SMTP on the free plan and the separate cold domain, and every email (Day 1, 3, 7) will include a valid postal address and a plain opt-out line (for example, "Reply 'unsubscribe' and I won't email you again"), honored by the existing unsubscribe classification.

## Options considered
| Option | Cost (build / run) | Time | Risk | Fit to priorities | Verdict |
|---|---|---|---|---|---|
| A. Brevo free plan (300/day) + compliance footer | Small template change / $0 | Low | Low | Fits 50–90/day | Recommended |
| B. Brevo Starter (no daily cap) | From $9/month | — | Low | Not needed at 50–90/day | Rejected for now |
| C. Keep templates as they are | None | — | Legal exposure per email | Not acceptable | Rejected |

## Consequences
- **Positive:** Lower legal risk; an easy opt-out also lowers spam complaints, which helps deliverability.
- **Negative / trade-offs accepted:** Warda must choose which postal address appears in emails (a registered PO box or private mailbox is allowed).
- **Follow-ups / revisit triggers:** Move to a paid Brevo plan if volume ever needs more than 300/day.

## Evidence
- Brevo help centre: the Free plan includes 300 email sends per day, resets daily, no rollover — https://help.brevo.com/hc/en-us/articles/208580669 (checked 2026-10-06).
- FTC, "CAN-SPAM Act: A Compliance Guide for Business" — https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business (requirements read via sources quoting it, 2026-10-06).
- `agents/email_agent.py` templates (read 2026-10-06).
- Assumptions still unconfirmed: which postal address Warda will use; whether Brevo's free-plan branding applies to SMTP sends (not checked).

## Rules for the code (what Claude Code must follow)
- Every outbound template must include the postal address and opt-out line; never remove them.
- A contact classified `unsubscribe` must never receive another email.
- Keep emails plain text.
