from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional

from config.settings import Settings, get_settings
from core.brevo_sender import BrevoSender
from core.database import ContactRepository
from core.exceptions import ConfigurationError, DatabaseError
from core.logging_config import get_logger

# ── Email templates ───────────────────────────────────────────────────────────

_SUBJECT_DAY1 = "Quick idea for you, {first_name}"
_SUBJECT_DAY3 = "Still there, {first_name}?"
_SUBJECT_DAY7 = "Last note for you, {first_name}"

_BODY_DAY1 = """\
Hey {first_name},

{personalized_line}

I help businesses figure out where AI can actually save time - could be leads, scheduling, reporting, customer communication, internal tools, or something specific to how you run things day to day. Every business is different, so the workflow gets built around what's actually slowing you down.

For my first 10 customers, I'm building and implementing this on your systems completely free - no charge. We hop on a call, I look at what you need, and if it's a fit, I set the whole thing up for you at zero cost. You'd only ever cover the maintenance fee once it's live.

Worth a 15-minute chat? I'm just an email away if you have questions, or you can book a time directly through my site: {cta_url}

Warda Khan
ConvertWithAI\
"""

_BODY_DAY3 = """\
Hey {first_name},

Just bumping this up.

I'm building and implementing custom AI workflows free for my first 10 customers - no charge on my end, you'd only cover the maintenance fee once it's live. Could be leads, admin, scheduling, or anything else eating up your time. A few spots are already taken.

15 minutes could save you hours of manual work every week. Just reply to this email with any questions, or book directly here: {cta_url}\
"""

_BODY_DAY7 = """\
Hey {first_name},

Won't bug you after this.

If manual work, disconnected tools, and admin overhead aren't a problem for you - we're not a fit.

If they are - this is a free custom AI build for one of my last 10 founding spots. No cost, just a maintenance fee once it's live. I'm just an email away, or you can book a time here: {cta_url}\
"""


@dataclasses.dataclass
class EmailResult:
    day1_sent: int = 0
    day3_sent: int = 0
    day7_sent: int = 0
    errors: int = 0

    @property
    def total_sent(self) -> int:
        return self.day1_sent + self.day3_sent + self.day7_sent


class EmailAgent:
    """
    Sends the 3-email cold outreach sequence via Brevo SMTP.

    Each run respects a daily budget across all three sequence steps:
      Day 1 - cold intro to status='personalized' leads (highest priority)
      Day 3 - follow-up to leads contacted 3+ days ago with no reply
      Day 7 - urgency email to leads contacted 7+ days ago with no reply

    Stops immediately if Brevo authentication fails (ConfigurationError).
    """

    def __init__(
        self,
        contacts: ContactRepository,
        sender: BrevoSender,
        settings: Optional[Settings] = None,
    ) -> None:
        self._contacts = contacts
        self._sender = sender
        self._s = settings or get_settings()
        self.logger = get_logger("agents.email_agent")

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, daily_limit: int = 0) -> EmailResult:
        """Send emails up to daily_limit total across all three sequence days."""
        budget = daily_limit or self._s.email_daily_limit
        result = EmailResult()

        self.logger.info("EmailAgent START - daily_budget=%d", budget)

        remaining = self._run_day1(result, budget)
        if remaining > 0:
            remaining = self._run_day3(result, remaining)
        if remaining > 0:
            self._run_day7(result, remaining)

        self.logger.info(
            "EmailAgent DONE - day1=%d day3=%d day7=%d errors=%d total=%d",
            result.day1_sent, result.day3_sent, result.day7_sent,
            result.errors, result.total_sent,
        )
        return result

    # ── Sequence steps ────────────────────────────────────────────────────────

    def _run_day1(self, result: EmailResult, budget: int) -> int:
        """Send Day-1 intro emails. Returns remaining budget."""
        contacts = self._contacts.get_personalized_unsent(limit=budget)
        if not contacts:
            return budget

        self.logger.info("Day 1 - %d contacts to email", len(contacts))
        for contact in contacts:
            if not self._send(contact, day=1, result=result):
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                self._contacts.update_email_sent(contact["id"], now_iso)
                result.day1_sent += 1
            except DatabaseError as exc:
                self.logger.error("DB update failed after Day-1 send for %s: %s", contact["name"], exc)
                result.errors += 1

        return budget - result.day1_sent

    def _run_day3(self, result: EmailResult, budget: int) -> int:
        """Send Day-3 follow-up emails. Returns remaining budget."""
        contacts = self._contacts.get_due_day3(limit=budget)
        if not contacts:
            return budget

        self.logger.info("Day 3 - %d follow-ups due", len(contacts))
        sent = 0
        for contact in contacts:
            if not self._send(contact, day=3, result=result):
                continue
            try:
                self._contacts.update_last_contacted(contact["id"])
                sent += 1
            except DatabaseError as exc:
                self.logger.error("DB update failed after Day-3 send for %s: %s", contact["name"], exc)
                result.errors += 1

        result.day3_sent = sent
        return budget - sent

    def _run_day7(self, result: EmailResult, budget: int) -> int:
        """Send Day-7 urgency emails. Returns remaining budget."""
        contacts = self._contacts.get_due_day7(limit=budget)
        if not contacts:
            return budget

        self.logger.info("Day 7 - %d urgency emails due", len(contacts))
        sent = 0
        for contact in contacts:
            if not self._send(contact, day=7, result=result):
                continue
            try:
                self._contacts.update_last_contacted(contact["id"])
                sent += 1
            except DatabaseError as exc:
                self.logger.error("DB update failed after Day-7 send for %s: %s", contact["name"], exc)
                result.errors += 1

        result.day7_sent = sent
        return budget - sent

    # ── Single-contact send (dashboard review flow) ─────────────────────────────

    def send_one(self, contact: dict, subject: str, body: str, day: int) -> bool:
        """
        Send an already-finalized subject/body (possibly user-edited/polished) to
        one specific contact, and update its DB status accordingly. Used by the
        review dashboard, where content is approved per-lead rather than in a batch.
        """
        try:
            ok = self._sender.send(
                to_email=contact["email"],
                to_name=contact["name"],
                subject=subject,
                body=body,
            )
        except ConfigurationError:
            raise  # auth failure - propagate, stop immediately
        except Exception as exc:
            self.logger.error("  [ERR] Day %d send_one error | %s: %s", day, contact["name"], exc)
            return False

        if not ok:
            self.logger.warning("  [ERR] Day %d send_one failed | %s", day, contact["name"])
            return False

        self.logger.info("  [OK] Day %d | %s <%s>", day, contact["name"], contact["email"])
        try:
            if day == 1:
                now_iso = datetime.now(timezone.utc).isoformat()
                self._contacts.update_email_sent(contact["id"], now_iso)
            else:
                self._contacts.update_last_contacted(contact["id"])
        except DatabaseError as exc:
            self.logger.error("DB update failed after Day-%d send_one for %s: %s", day, contact["name"], exc)
        return True

    # ── Send helper ───────────────────────────────────────────────────────────

    def _send(self, lead: dict, day: int, result: EmailResult) -> bool:
        """Build and send one email. Returns True on success. Raises on auth failure."""
        first_name = self._first_name(lead["name"])
        subject, body = self._build_email(
            day=day,
            first_name=first_name,
            personalized_line=lead.get("personalized_line", ""),
            cta_url=self._tracking_url(lead["id"]),
        )
        try:
            ok = self._sender.send(
                to_email=lead["email"],
                to_name=lead["name"],
                subject=subject,
                body=body,
            )
            if ok:
                self.logger.info("  [OK] Day %d | %s <%s>", day, lead["name"], lead["email"])
            else:
                self.logger.warning("  [ERR] Day %d send failed | %s", day, lead["name"])
                result.errors += 1
            return ok
        except ConfigurationError:
            raise  # auth failure - propagate to runner, stop immediately
        except Exception as exc:
            self.logger.error("  [ERR] Day %d error | %s: %s", day, lead["name"], exc)
            result.errors += 1
            return False

    def _tracking_url(self, lead_id: str) -> str:
        """Return a per-lead tracking URL if WEBHOOK_BASE_URL is set, else the plain CTA."""
        base = self._s.webhook_base_url
        if base:
            return f"{base.rstrip('/')}/track?ref={lead_id}"
        return self._s.email_cta_url

    def _build_email(
        self,
        day: int,
        first_name: str,
        personalized_line: str,
        cta_url: Optional[str] = None,
    ) -> tuple[str, str]:
        """Return (subject, body) for the given sequence day."""
        cta = cta_url or self._s.email_cta_url
        ctx = {"first_name": first_name, "cta_url": cta}

        if day == 1:
            subject, opener = self._split_personalized(personalized_line)
            return (
                subject or _SUBJECT_DAY1.format(**ctx),
                _BODY_DAY1.format(personalized_line=opener, **ctx),
            )
        if day == 3:
            return _SUBJECT_DAY3.format(**ctx), _BODY_DAY3.format(**ctx)
        return _SUBJECT_DAY7.format(**ctx), _BODY_DAY7.format(**ctx)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _split_personalized(personalized_line: str) -> tuple[Optional[str], str]:
        """Split a 'subject ||| opener' personalized_line into (subject, opener).

        Falls back to (None, personalized_line) if no GPT-generated subject is present
        (e.g. older data written before subject generation was added).
        """
        from agents.personalization import DELIM
        if DELIM in personalized_line:
            subject, opener = personalized_line.split(DELIM, 1)
            return subject.strip() or None, opener.strip()
        return None, personalized_line

    @staticmethod
    def _first_name(full_name: str) -> str:
        """Extract the first word of a full name."""
        return full_name.strip().split()[0] if full_name.strip() else full_name
