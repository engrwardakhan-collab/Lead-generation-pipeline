from __future__ import annotations

import dataclasses
import json
import re
from typing import Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError
from pydantic import BaseModel, ValidationError

from config.settings import Settings, get_settings
from core.content_guard import company_mismatch, echoed_text_is_grounded, terms_not_grounded
from core.database import ContactRepository, LeadRepository
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger

_SYSTEM_PROMPT = (
    "You write cold outreach content for business owners and executives. You will be "
    "given a business's name and a short raw description scraped from the web. Write "
    "ONE opening line using ONLY the business name and that description. Do not "
    "assume, invent, or reference any industry, service, achievement, or detail not "
    "explicitly present in the description text.\n"
    "Return ONLY a JSON object with exactly five keys:\n"
    '"subject" - a short, specific, curiosity-driven email subject line (max 8 words). '
    "No spammy words like 'free' or exclamation marks.\n"
    '"opener" - exactly two sentences, separated by a single space:\n'
    "  1. An opening line noticing something concrete from the business name or "
    "description below. Max 15 words.\n"
    "  2. A specific, open-ended question about what's taking up the most time in "
    "their day-to-day business right now - could be leads, scheduling, reporting, "
    "customer communication, internal tools, or anything else. Max 20 words.\n"
    '"company_mentioned" - the exact company name you used in the opener, if any, else null.\n'
    '"source_text_used" - the exact verbatim snippet of the description below that you '
    "based the opening line on, copied word-for-word. Empty string if you didn't use "
    "any specific detail from it.\n"
    '"grounded" - true if every claim in your opener is directly supported by the '
    "business name or description given to you; false if you had to guess or infer "
    "anything not explicitly stated.\n"
    "Rules:\n"
    "- Never state or imply an industry, activity, or fact that isn't explicitly in "
    "the description text\n"
    "- \"opener\" must NOT include a greeting - the email template adds 'Hey [Name],' before it\n"
    "- Output ONLY the JSON object, no markdown, no explanation"
)

_USER_TEMPLATE = """\
Name: {name}
Title: {title}
Company: {brokerage}
City: {city}{listing_line}
Business description (raw, scraped - the ONLY source of truth for any business fact): {description}

Write the subject and opener, grounded ONLY in the above."""

_CONSISTENCY_SYSTEM = (
    "You fact-check one short line of cold-outreach copy against a business's raw "
    "scraped description. Return ONLY a JSON object with two keys: "
    '"contradicts" (true or false) and "reason" (a short string, empty if false). '
    '"contradicts" is true if the line states or implies ANY fact about the business '
    "(industry, activity, achievement, specialty, or anything else) that is not "
    "directly supported by the description text. Minor rephrasing is fine; inventing "
    "new facts is not. Output ONLY the JSON object."
)

_MAX_TOKENS = 180
_CONSISTENCY_MAX_TOKENS = 80
_WORD_LIMIT = 35
_SUBJECT_WORD_LIMIT = 8
_MAX_ATTEMPTS = 2  # each attempt is a brand-new, isolated API call - no shared history

# Stored in the single personalized_line DB column as "subject<DELIM>opener"
DELIM = " ||| "

# Hardcoded, zero-risk fallback subject. Distinct enough that a real GPT-generated
# subject landing on it by coincidence would be a harmless false positive.
_FALLBACK_SUBJECT = "Quick question for you"


class _PersonalizationOutput(BaseModel):
    subject: str = ""
    opener: str = ""
    company_mentioned: Optional[str] = None
    source_text_used: Optional[str] = None
    grounded: bool = False


class _ConsistencyCheck(BaseModel):
    contradicts: bool = False
    reason: str = ""


@dataclasses.dataclass
class PersonalizationResult:
    written: int = 0
    skipped: int = 0  # not currently set by the guarded pipeline below; kept for callers that log it
    errors: int = 0
    fallback: int = 0  # no raw description at all -> generic fallback used, no GPT call made
    flagged: int = 0   # description existed but every attempt failed grounding validation -> quarantined


def is_generic_fallback(personalized_line: str) -> bool:
    """True if this line is the hardcoded fallback (no verified personalization),
    so the dashboard can flag it for extra scrutiny before approval."""
    return personalized_line.startswith(_FALLBACK_SUBJECT + DELIM)


class PersonalizationAgent:
    """
    Reads new contacts (with email) from Supabase, generates a GPT-4o-mini opening line
    for each grounded strictly in the lead's raw scraped description, and writes it back
    via update_personalized_line() -> status = 'personalized'.

    Safety pipeline per lead:
      1. No description_raw at all -> generic fallback (name/city only), no GPT call.
      2. Otherwise, generate + validate. Validation is layered:
         a. Model self-reports grounded=false -> reject.
         b. Model's echoed source_text_used must appear verbatim in the real
            description (core.content_guard.echoed_text_is_grounded).
         c. Self-reported company must match the lead's real company on file.
         d. Cheap keyword scan for industry-cluster terms not present in the
            real description.
         e. A second, independent GPT-4o-mini call fact-checks the line against
            the description for anything the first three checks might miss.
      3. A validation failure triggers one retry (fresh, isolated call).
      4. Still failing after retry -> the contact is FLAGGED (status='flagged',
         excluded from the normal review queue) for a human to inspect on the
         /flagged screen - never silently substituted or sent.
    """

    def __init__(
        self,
        contacts: ContactRepository,
        db: LeadRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        self._contacts = contacts
        self._db = db
        self._s = settings or get_settings()
        self.logger = get_logger("agents.personalization")
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for PersonalizationAgent")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, max_leads: int = 0, dry_run: bool = False) -> PersonalizationResult:
        """Fetch unpersonalized contacts and write a GPT opening line for each.

        dry_run=True generates and logs each line WITHOUT calling
        update_personalized_line or update_flag - nothing is written to the
        contacts table, so nothing can end up in a human's send queue or
        quarantine. Use this for any manual check of prompt/output quality
        against real leads; never call run() with dry_run=False just to
        "see what it generates."
        """
        budget = max_leads or self._s.enricher_max_per_run
        contacts = self._contacts.get_unpersonalized(limit=budget)

        result = PersonalizationResult()
        self.logger.info(
            "PersonalizationAgent START - budget=%d contacts=%d dry_run=%s",
            budget, len(contacts), dry_run,
        )

        for idx, contact in enumerate(contacts, 1):
            self.logger.debug("[%d/%d] %s", idx, len(contacts), contact["name"])
            try:
                lead = self._db.get_by_id(contact["lead_id"]) or {}
                brokerage = lead.get("brokerage") or lead.get("name")
                line, used_fallback, flag_reason = self._generate_line(
                    name=contact["name"],
                    title=contact.get("title"),
                    brokerage=brokerage,
                    location=lead.get("location"),
                    listing_count=lead.get("listing_count"),
                    description_raw=lead.get("description_raw"),
                )

                if flag_reason:
                    if not dry_run:
                        self._contacts.update_flag(contact["id"], flag_reason, rejected_line=line)
                    result.flagged += 1
                    self.logger.warning("  [FLAGGED] %s: %s", contact["name"], flag_reason)
                    continue

                if not dry_run:
                    self._contacts.update_personalized_line(contact["id"], line)
                result.written += 1
                if used_fallback:
                    result.fallback += 1
                tag = "FALLBACK" if used_fallback else ("DRY-RUN" if dry_run else "OK")
                self.logger.info("  [%s] %s -> \"%s\"", tag, contact["name"], line)
            except RateLimitError:
                self.logger.error("OpenAI rate limit reached - stopping personalization run")
                break
            except Exception as exc:
                self.logger.error("  [ERR] Error for %s: %s", contact["name"], exc)
                result.errors += 1

        self.logger.info(
            "PersonalizationAgent DONE - written=%d fallback=%d flagged=%d errors=%d dry_run=%s",
            result.written, result.fallback, result.flagged, result.errors, dry_run,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _generate_line(
        self,
        name: str,
        title: Optional[str],
        brokerage: Optional[str],
        location: Optional[str],
        listing_count: Optional[int],
        description_raw: Optional[str],
    ) -> tuple[str, bool, Optional[str]]:
        """Return (personalized_line, used_fallback, flag_reason).

        flag_reason is only set when description_raw existed but no attempt
        could pass validation - the caller must quarantine this contact rather
        than send personalized_line as-is."""
        city = self._extract_city(location)

        if not description_raw:
            return self._generic_fallback(city), True, None

        listing_line = f"\nActive listings: {listing_count}" if listing_count is not None else ""
        prompt = _USER_TEMPLATE.format(
            name=name,
            title=title or "their role",
            brokerage=brokerage or "their company",
            city=city,
            listing_line=listing_line,
            description=description_raw,
        )

        last_line = ""
        last_violation: Optional[str] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            output = self._call_gpt(prompt)
            if output is None:
                continue
            line = f"{output.subject}{DELIM}{output.opener}" if output.subject else output.opener
            violation = self._validate(output, name, brokerage, description_raw)
            if violation is None:
                return line, False, None
            last_line, last_violation = line, violation
            self.logger.warning(
                "  [BLOCKED %d/%d] %s: %s", attempt, _MAX_ATTEMPTS, name, violation,
            )

        self.logger.warning(
            "  [FLAGGED] %s - failed grounding validation after %d attempts", name, _MAX_ATTEMPTS,
        )
        return last_line, False, (last_violation or "failed grounding validation")

    def _call_gpt(self, prompt: str) -> Optional[_PersonalizationOutput]:
        """One fresh, isolated API call - no conversation history from prior leads
        or prior attempts is ever included."""
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=_MAX_TOKENS,
                temperature=0.7,
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
            output = _PersonalizationOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError):
            self.logger.debug("GPT returned invalid personalization output: %s", raw[:200])
            return None

        if not output.opener.strip():
            return None

        output.subject = self._enforce_word_limit(output.subject.strip().strip('"'), limit=_SUBJECT_WORD_LIMIT)
        output.opener = self._enforce_word_limit(output.opener.strip())
        return output

    def _validate(
        self,
        output: _PersonalizationOutput,
        name: str,
        actual_company: Optional[str],
        description_raw: str,
    ) -> Optional[str]:
        """Return a human-readable violation reason if the draft isn't grounded, else None.
        Cheap checks run first and fail fast; the second GPT call only runs if those pass."""
        if not output.grounded:
            return "self-reported grounded=false"
        if not echoed_text_is_grounded(output.source_text_used, description_raw):
            return f"source_text_used not found verbatim in description: '{output.source_text_used}'"
        if company_mismatch(output.company_mentioned, actual_company):
            return f"claimed company '{output.company_mentioned}' does not match '{actual_company}'"
        ungrounded_terms = terms_not_grounded(f"{output.subject} {output.opener}", description_raw)
        if ungrounded_terms:
            return f"opener/subject contains term(s) not in description: {ungrounded_terms}"
        return self._consistency_check(name, description_raw, output.subject, output.opener)

    def _consistency_check(
        self, name: str, description_raw: str, subject: str, opener: str,
    ) -> Optional[str]:
        """Fresh, independent GPT-4o-mini call: does this line contradict or invent
        something beyond the raw description? Returns a violation reason if so, else
        None. Fails CLOSED (treated as a violation) if the check itself can't be
        parsed - an unreadable safety check is not the same as a passed one."""
        prompt = (
            f"Business name: {name}\nBusiness description: {description_raw}\n\n"
            f"Cold-outreach line to check:\nSubject: {subject}\nOpener: {opener}\n\n"
            "Does the line contradict or invent something beyond the description?"
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

    @staticmethod
    def _generic_fallback(city: str) -> str:
        """Hardcoded, zero-risk fallback - no invented facts, just the verified city."""
        opener = (
            f"I help business owners in {city} figure out where AI can actually save them "
            "time day to day - what's eating up most of yours right now?"
        )
        return f"{_FALLBACK_SUBJECT}{DELIM}{opener}"

    @staticmethod
    def _extract_city(location: Optional[str]) -> str:
        """Return just the city portion of 'City, ST' or the full string if no comma."""
        if not location:
            return "your market"
        return location.split(",")[0].strip() or location

    @staticmethod
    def _enforce_word_limit(text: str, limit: int = _WORD_LIMIT) -> str:
        """Trim to the given word limit if GPT overruns it."""
        words = text.split()
        if len(words) <= limit:
            return text
        return " ".join(words[:limit])
