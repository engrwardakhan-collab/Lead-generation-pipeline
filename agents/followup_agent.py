from __future__ import annotations

import json
import re
from typing import Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, ValidationError

from config.settings import Settings, get_settings
from core.content_guard import company_mismatch, echoed_text_is_grounded, terms_not_grounded
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger
from core.sent_log import SentEmailLog

_SYSTEM_PROMPT = (
    "You write a short Day-{day} cold-outreach follow-up email, referencing an "
    "original email already sent to this person - do not repeat it verbatim, "
    "acknowledge you reached out before. Keep the same offer: free build/"
    "implementation for the first 10 customers, they'd only cover a maintenance "
    "fee once live. Tone: direct, brief, never salesy.\n"
    "You are also given a short raw description of the business, scraped from the "
    "web. Do not assume, invent, or reference any industry, service, achievement, "
    "or detail about the business that isn't explicitly present in that description "
    "or the original email.\n"
    'Return ONLY a JSON object with exactly five keys: "subject", "body", '
    '"company_mentioned", "source_text_used", and "grounded".\n'
    "The body must NOT include a greeting (the caller adds 'Hey [Name],') or a signature.\n"
    '"company_mentioned" - the exact company name you used in the body, if any, else null.\n'
    '"source_text_used" - the exact verbatim snippet of the business description below '
    "that you based any new (non-Day-1) detail on, copied word-for-word. Empty string "
    "if you didn't use any detail from it beyond what the original email already said.\n"
    '"grounded" - true if every claim in the body is directly supported by the '
    "original email or the business description; false if you had to guess or infer "
    "anything not explicitly stated."
)

_USER_TEMPLATE = """\
Name: {name}
Title: {title}
Company: {brokerage}
City: {city}
Business description (raw, scraped): {description}

Original email already sent:
Subject: {original_subject}
Body: {original_body}

Write the Day-{day} follow-up."""

_CONSISTENCY_SYSTEM = (
    "You fact-check one short follow-up email against a business's raw scraped "
    "description and the original email it references. Return ONLY a JSON object "
    'with two keys: "contradicts" (true or false) and "reason" (a short string, '
    'empty if false). "contradicts" is true if the follow-up states or implies ANY '
    "fact about the business that is not directly supported by the description or "
    "the original email. Minor rephrasing is fine; inventing new facts is not. "
    "Output ONLY the JSON object."
)

_MAX_TOKENS = 320
_CONSISTENCY_MAX_TOKENS = 80
_MAX_ATTEMPTS = 2  # each attempt is a brand-new, isolated API call - no shared history


class _FollowupOutput(BaseModel):
    subject: str = ""
    body: str = ""
    company_mentioned: Optional[str] = None
    source_text_used: Optional[str] = None
    grounded: bool = False


class _ConsistencyCheck(BaseModel):
    contradicts: bool = False
    reason: str = ""


class FollowupAgent:
    """
    Generates a Day-3/Day-7 follow-up draft for one contact, using whatever was
    actually sent as Day-1 context (from SentEmailLog if available — reflects any
    user edits — falling back to the stored personalized_line for contacts sent
    before the dashboard existed).

    Same grounding pipeline as PersonalizationAgent: the model must self-report
    which company/source text it used, that self-report is cross-checked against
    the lead's real description, a cheap keyword scan catches ungrounded terms,
    and a second independent GPT call fact-checks the draft. A draft that fails
    validation is retried once (fresh call); if it fails again, generate() returns
    None and the dashboard skips this contact for the session rather than sending
    an unverified claim. Unlike PersonalizationAgent, a failure here does NOT
    change the contact's DB status - these are already-contacted leads mid-sequence,
    and flagging would corrupt Day-3/7 eligibility tracking.
    """

    def __init__(
        self,
        sent_log: Optional[SentEmailLog] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._sent_log = sent_log or SentEmailLog()
        self._s = settings or get_settings()
        self.logger = get_logger("agents.followup_agent")
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for FollowupAgent")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)

    def generate(self, contact: dict, lead: dict, day: int) -> Optional[tuple[str, str]]:
        """Return (subject, body) for the given contact's Day-3/7 follow-up, or None
        if there's no original send to reference, or no safe draft could be produced."""
        original_subject, original_body = self._original_send(contact)
        if not original_subject and not original_body:
            self.logger.warning(
                "No original Day-1 content found for %s - cannot draft follow-up", contact["name"]
            )
            return None

        brokerage = lead.get("brokerage") or lead.get("name")
        description_raw = lead.get("description_raw") or ""
        city = (lead.get("location") or "").split(",")[0].strip() or "your market"
        prompt = _USER_TEMPLATE.format(
            name=contact["name"],
            title=contact.get("title") or "their role",
            brokerage=brokerage or "their company",
            city=city,
            description=description_raw or "(none scraped)",
            original_subject=original_subject,
            original_body=original_body,
            day=day,
        )
        system = _SYSTEM_PROMPT.format(day=day)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            output = self._call_gpt(system, prompt, contact["name"])
            if output is None:
                continue
            violation = self._validate(output, contact["name"], brokerage, description_raw)
            if violation is None:
                return output.subject, output.body
            self.logger.warning(
                "  [BLOCKED %d/%d] %s: %s", attempt, _MAX_ATTEMPTS, contact["name"], violation,
            )

        self.logger.warning(
            "  [SKIPPED] %s - every follow-up attempt failed grounding validation", contact["name"],
        )
        return None

    @staticmethod
    def assemble(first_name: str, gpt_body: str) -> str:
        """Wrap GPT's follow-up body with the same greeting/signoff style as Day-1 emails."""
        return f"Hey {first_name},\n\n{gpt_body}\n\nWarda Khan\nConvertWithAI"

    # ── Private helpers ───────────────────────────────────────────────────────

    def _call_gpt(self, system: str, prompt: str, name: str) -> Optional[_FollowupOutput]:
        """One fresh, isolated API call - no conversation history from prior leads
        or prior attempts is ever included."""
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=_MAX_TOKENS,
                temperature=0.6,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        if not raw:
            return None

        try:
            output = _FollowupOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            self.logger.warning("Follow-up response was not valid JSON for %s", name)
            return None

        output.subject = output.subject.strip()
        output.body = output.body.strip()
        if not output.subject or not output.body:
            return None
        return output

    def _validate(
        self,
        output: _FollowupOutput,
        name: str,
        actual_company: Optional[str],
        description_raw: str,
    ) -> Optional[str]:
        """Return a human-readable violation reason if the draft isn't grounded, else None.
        source_text_used is only checked when the model actually claims to have used a
        detail from the description - follow-ups often rely solely on the original Day-1
        email, which is fine and doesn't require a description echo."""
        if not output.grounded:
            return "self-reported grounded=false"
        if output.source_text_used and not echoed_text_is_grounded(output.source_text_used, description_raw):
            return f"source_text_used not found verbatim in description: '{output.source_text_used}'"
        if company_mismatch(output.company_mentioned, actual_company):
            return f"claimed company '{output.company_mentioned}' does not match '{actual_company}'"
        ungrounded_terms = terms_not_grounded(f"{output.subject} {output.body}", description_raw)
        if ungrounded_terms:
            return f"subject/body contains term(s) not in description: {ungrounded_terms}"
        return self._consistency_check(name, description_raw, output.subject, output.body)

    def _consistency_check(
        self, name: str, description_raw: str, subject: str, body: str,
    ) -> Optional[str]:
        """Fresh, independent GPT-4o-mini call: does this follow-up contradict or invent
        something beyond the raw description? Fails CLOSED if unparseable."""
        prompt = (
            f"Business name: {name}\nBusiness description: {description_raw or '(none scraped)'}\n\n"
            f"Follow-up email to check:\nSubject: {subject}\nBody: {body}\n\n"
            "Does the email contradict or invent something beyond the description?"
        )
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _CONSISTENCY_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=_CONSISTENCY_MAX_TOKENS,
                temperature=0,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            check = _ConsistencyCheck.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            self.logger.warning("Consistency check returned invalid output for %s - failing safe", name)
            return "consistency check response was invalid - failing safe"

        if check.contradicts:
            return f"consistency check: {check.reason or 'contradicts the business description'}"
        return None

    def _original_send(self, contact: dict) -> tuple[str, str]:
        """Return (subject, body) of the original Day-1 send for this contact."""
        records = [r for r in self._sent_log.for_contact(contact["id"]) if r.get("day") == 1]
        if records:
            latest = records[-1]
            return latest.get("subject", ""), latest.get("body", "")

        # Fallback: contact was sent before the dashboard's sent-log existed
        # (e.g. via the CLI) — reconstruct from the stored personalized_line.
        from agents.personalization import DELIM
        personalized_line = contact.get("personalized_line") or ""
        if DELIM in personalized_line:
            subject, opener = personalized_line.split(DELIM, 1)
            return subject.strip(), opener.strip()
        return "", personalized_line.strip()
