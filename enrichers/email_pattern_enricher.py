"""
Email pattern enricher for contacts without a known email address.

Strategy:
  1. Fetch contacts where email IS NULL and status = 'new'
  2. Get the brokerage domain from the linked lead's website_url
  3. Generate common first/last name patterns (herbert@, hcoleman@, etc.)
  4. Run SmtpVerifier on each candidate until one passes RCPT TO
  5. Save the verified email back to the contacts table
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Optional
from urllib.parse import urlparse

from config.settings import Settings, get_settings
from core.database import ContactRepository, LeadRepository
from core.exceptions import DatabaseError
from core.logging_config import get_logger
from core.smtp_verifier import SmtpVerifier

_NAME_SUFFIXES: frozenset[str] = frozenset({
    "jr", "sr", "ii", "iii", "iv", "v", "esq", "phd", "md",
})

_GENERIC_PREFIXES: tuple[str, ...] = (
    "info", "contact", "admin", "hello", "office", "broker",
)


@dataclasses.dataclass
class EmailPatternResult:
    contacts_processed: int = 0
    emails_found: int = 0
    not_found: int = 0
    errors: int = 0


class EmailPatternEnricher:
    """
    Finds email addresses for contacts that have none by guessing common
    patterns and verifying each via SMTP RCPT TO handshake (no email sent).
    """

    def __init__(
        self,
        db: LeadRepository,
        contacts: ContactRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        self._db = db
        self._contacts = contacts
        self._s = settings or get_settings()
        self._verifier = SmtpVerifier()
        self.logger = get_logger("enrichers.email_pattern")

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, max_contacts: int = 0) -> EmailPatternResult:
        """Find emails for contacts without one. Returns counts of what happened."""
        budget = max_contacts or self._s.enricher_max_per_run
        pending = self._contacts.get_contacts_without_email(limit=budget)
        result = EmailPatternResult()

        self.logger.info(
            "EmailPatternEnricher START - budget=%d contacts=%d",
            budget, len(pending),
        )

        for idx, contact in enumerate(pending, 1):
            self.logger.debug("[%d/%d] %s", idx, len(pending), contact["name"])
            try:
                self._process_one(contact, result)
            except DatabaseError:
                raise
            except Exception as exc:
                self.logger.error("Error processing contact %s: %s", contact["name"], exc)
                result.errors += 1

        self.logger.info(
            "EmailPatternEnricher DONE - processed=%d found=%d not_found=%d errors=%d",
            result.contacts_processed, result.emails_found, result.not_found, result.errors,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _process_one(self, contact: dict, result: EmailPatternResult) -> None:
        result.contacts_processed += 1

        lead = self._db.get_by_id(contact["lead_id"])
        if not lead or not lead.get("website_url"):
            self.logger.debug("No website_url for lead linked to %s - skipping", contact["name"])
            result.not_found += 1
            return

        domain = _extract_domain(lead["website_url"])
        if not domain:
            self.logger.debug("Could not parse domain from %s", lead["website_url"])
            result.not_found += 1
            return

        patterns = _generate_patterns(contact["name"], domain)
        self.logger.info(
            "Checking %d patterns for %s @%s", len(patterns), contact["name"], domain,
        )

        for candidate in patterns:
            self.logger.debug("  Trying %s ...", candidate)
            if self._verifier.verify(candidate):
                self._contacts.update_email(contact["id"], candidate)
                result.emails_found += 1
                self.logger.info("[OK] %s -> %s", contact["name"], candidate)
                return

        result.not_found += 1
        self.logger.info("[--] No verified email found for %s @%s", contact["name"], domain)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _extract_domain(url: str) -> Optional[str]:
    """Return the bare domain (no www.) from a URL, or None on parse failure."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return hostname.removeprefix("www.") or None
    except Exception:
        return None


def _slugify(text: str) -> str:
    """Lowercase, ASCII-only letters."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def _parse_name(full_name: str) -> tuple[str, str]:
    """Return (first, last) after stripping suffixes and punctuation."""
    parts = [p.strip(".,") for p in full_name.split()]
    parts = [p for p in parts if p and _slugify(p) not in _NAME_SUFFIXES]
    if not parts:
        return ("", "")
    first = _slugify(parts[0])
    last = _slugify(parts[-1]) if len(parts) > 1 else ""
    return first, last


def _generate_patterns(full_name: str, domain: str) -> list[str]:
    """
    Return candidate email addresses in priority order.
    Person-specific patterns come before generic fallbacks.
    """
    first, last = _parse_name(full_name)
    candidates: list[str] = []

    if first and last:
        candidates = [
            f"{first}@{domain}",
            f"{first}{last}@{domain}",
            f"{first}.{last}@{domain}",
            f"{first[0]}{last}@{domain}",
            f"{first[0]}.{last}@{domain}",
            f"{first}_{last}@{domain}",
        ]
    elif first:
        candidates = [f"{first}@{domain}"]

    for prefix in _GENERIC_PREFIXES:
        candidates.append(f"{prefix}@{domain}")

    return candidates
