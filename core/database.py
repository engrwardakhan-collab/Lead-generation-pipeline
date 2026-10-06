from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from postgrest.exceptions import APIError
from supabase import Client, create_client

from config.settings import get_settings
from core.exceptions import DatabaseError
from core.logging_config import get_logger
from core.models import Contact, Lead

logger = get_logger("database")

_UNIQUE_VIOLATION = "23505"  # Postgres error code for a UNIQUE constraint violation


@lru_cache(maxsize=1)
def _get_client() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_key)


_REPLY_STATUS: dict[str, str] = {
    "interested": "interested",
    "unsubscribe": "unsubscribed",
    "not_interested": "replied",
}


class LeadRepository:
    """
    All Supabase access for the leads table goes through here.
    Nothing else in the codebase should import supabase directly.
    """

    def __init__(self, table: Optional[str] = None) -> None:
        self._client = _get_client()
        self._table = table or get_settings().leads_table

    # ── Writes ────────────────────────────────────────────────────────────────

    def upsert(self, lead: Lead) -> bool:
        """Insert or update on profile_url. Returns True on success.

        Preserves the existing status on a re-scrape of an already-known lead —
        only a brand-new row gets status='new'. Without this, re-running the
        scraper resets already-enriched leads back to 'new' and causes
        TeamPageEnricher to wastefully redo already-completed work.
        """
        try:
            data = lead.to_db_dict()
            existing = (
                self._client.table(self._table)
                .select("id")
                .eq("profile_url", lead.profile_url)
                .limit(1)
                .execute()
            )
            if existing.data:
                data.pop("status", None)
            self._client.table(self._table).upsert(
                data,
                on_conflict="profile_url",
            ).execute()
            return True
        except Exception as exc:
            logger.error("upsert failed for '%s': %s", lead.name, exc)
            raise DatabaseError(f"upsert failed: {exc}") from exc

    def get_id_by_profile_url(self, profile_url: str) -> Optional[str]:
        """Return the id of the lead matching profile_url, or None if not found."""
        try:
            result = (
                self._client.table(self._table)
                .select("id")
                .eq("profile_url", profile_url)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0]["id"] if rows else None
        except Exception as exc:
            logger.error("get_id_by_profile_url failed for '%s': %s", profile_url, exc)
            raise DatabaseError(f"get_id_by_profile_url failed: {exc}") from exc

    def mark_enriched(self, lead_id: str, email: str) -> bool:
        """Set email and advance status to 'enriched' in a single atomic write."""
        try:
            self._client.table(self._table).update(
                {"email": email, "status": "enriched"}
            ).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("mark_enriched failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"mark_enriched failed: {exc}") from exc

    def update_email(self, lead_id: str, email: str) -> bool:
        try:
            self._client.table(self._table).update({"email": email}).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_email failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_email failed: {exc}") from exc

    def update_status(self, lead_id: str, status: str) -> bool:
        try:
            self._client.table(self._table).update({"status": status}).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_status failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_status failed: {exc}") from exc

    def update_personalized_line(self, lead_id: str, line: str) -> bool:
        """Save the GPT opening line and advance status to 'personalized'."""
        try:
            self._client.table(self._table).update({"personalized_line": line, "status": "personalized"}).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_personalized_line failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_personalized_line failed: {exc}") from exc

    def update_email_sent(self, lead_id: str, email_sent_at: str) -> bool:
        """Record Day-1 send timestamp, set last_contacted, and advance status to 'contacted'."""
        try:
            self._client.table(self._table).update({
                "email_sent_at": email_sent_at,
                "last_contacted": email_sent_at,
                "status": "contacted",
            }).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_email_sent failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_email_sent failed: {exc}") from exc

    def update_last_contacted(self, lead_id: str) -> bool:
        """Stamp last_contacted = now (called after Day-3 and Day-7 follow-up sends)."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self._client.table(self._table).update({"last_contacted": now_iso}).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_last_contacted failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_last_contacted failed: {exc}") from exc

    def update_reply(self, lead_id: str, classification: str) -> bool:
        """Record reply classification and advance status to its terminal state."""
        status = _REPLY_STATUS.get(classification, "replied")
        try:
            self._client.table(self._table).update({
                "reply_classification": classification,
                "status": status,
            }).eq("id", lead_id).execute()
            return True
        except Exception as exc:
            logger.error("update_reply failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"update_reply failed: {exc}") from exc

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_unenriched(self, limit: int) -> list[dict]:
        """Return leads where email IS NULL, oldest scraped first."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, brokerage, location, source, listing_count, profile_url, website_url")
                .is_("email", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_unenriched failed: %s", exc)
            raise DatabaseError(f"get_unenriched failed: {exc}") from exc

    def get_high_value_unenriched(self, min_listings: int, limit: int) -> list[dict]:
        """
        Return unenriched leads with a brokerage AND listing_count >= min_listings.
        Ordered by listing_count desc so the highest-value agents are enriched first.
        """
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, brokerage, location, source, listing_count, profile_url, website_url")
                .is_("email", "null")
                .not_.is_("brokerage", "null")
                .gte("listing_count", min_listings)
                .order("listing_count", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_high_value_unenriched failed: %s", exc)
            raise DatabaseError(f"get_high_value_unenriched failed: {exc}") from exc

    def get_enriched_unpersonalized(self, limit: int) -> list[dict]:
        """Return enriched leads that don't yet have a personalized opening line."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, brokerage, location, listing_count, email")
                .eq("status", "enriched")
                .is_("personalized_line", "null")
                .order("listing_count", desc=True, nullsfirst=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_enriched_unpersonalized failed: %s", exc)
            raise DatabaseError(f"get_enriched_unpersonalized failed: {exc}") from exc

    def get_personalized_unsent(self, limit: int) -> list[dict]:
        """Return leads ready to email — personalized but not yet contacted."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, email, personalized_line, location, brokerage")
                .eq("status", "personalized")
                .is_("email_sent_at", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_personalized_unsent failed: %s", exc)
            raise DatabaseError(f"get_personalized_unsent failed: {exc}") from exc

    def get_contacted_by_email(self, email: str) -> Optional[dict]:
        """Return a contacted lead matching the given email address, or None if not found / already classified."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, brokerage, location, email")
                .eq("email", email)
                .eq("status", "contacted")
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.error("get_contacted_by_email failed for %s: %s", email, exc)
            raise DatabaseError(f"get_contacted_by_email failed: {exc}") from exc

    def get_due_day3(self, limit: int) -> list[dict]:
        """Return contacted leads whose Day-3 follow-up is due (3+ days since Day-1, no contact since)."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            result = (
                self._client.table(self._table)
                .select("id, name, email")
                .eq("status", "contacted")
                .lte("email_sent_at", cutoff)
                .lte("last_contacted", cutoff)
                .order("email_sent_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_due_day3 failed: %s", exc)
            raise DatabaseError(f"get_due_day3 failed: {exc}") from exc

    def get_leads_for_team_scrape(self, limit: int) -> list[dict]:
        """Return new leads that have a website URL and haven't been team-scraped yet."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, website_url, profile_url")
                .eq("status", "new")
                .not_.is_("website_url", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_leads_for_team_scrape failed: %s", exc)
            raise DatabaseError(f"get_leads_for_team_scrape failed: {exc}") from exc

    def get_by_id(self, lead_id: str) -> Optional[dict]:
        """Return a single lead by primary key, or None if not found."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, website_url, profile_url, brokerage, location, listing_count, phone, description_raw")
                .eq("id", lead_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.error("get_by_id failed for id=%s: %s", lead_id, exc)
            raise DatabaseError(f"get_by_id failed: {exc}") from exc

    def get_due_day7(self, limit: int) -> list[dict]:
        """Return contacted leads whose Day-7 urgency email is due (7+ days since Day-1, 3+ days since last contact)."""
        try:
            cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cutoff_3d = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            result = (
                self._client.table(self._table)
                .select("id, name, email")
                .eq("status", "contacted")
                .lte("email_sent_at", cutoff_7d)
                .lte("last_contacted", cutoff_3d)
                .order("email_sent_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_due_day7 failed: %s", exc)
            raise DatabaseError(f"get_due_day7 failed: {exc}") from exc


# ── Result containers ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class ScrapingResult:
    city: str
    saved: int = 0
    skipped: int = 0
    errors: int = 0
    categories_tried: int = 0

    @property
    def total_processed(self) -> int:
        return self.saved + self.skipped + self.errors


@dataclasses.dataclass
class EnrichmentResult:
    enriched: int = 0
    not_found: int = 0
    errors: int = 0


@dataclasses.dataclass
class TeamEnrichmentResult:
    leads_processed: int = 0
    contacts_saved: int = 0
    leads_skipped: int = 0
    duplicates_skipped: int = 0
    errors: int = 0


_CONTACT_REPLY_STATUS: dict[str, str] = {
    "interested": "interested",
    "unsubscribe": "unsubscribed",
    "not_interested": "replied",
}

_CONTACTS_TABLE = "contacts"


class ContactRepository:
    """All Supabase access for the contacts table."""

    def __init__(self) -> None:
        self._client = _get_client()
        self._table = _CONTACTS_TABLE

    # ── Writes ────────────────────────────────────────────────────────────────

    def upsert(self, contact: Contact) -> bool:
        """Insert or update on profile_url. Returns True if the row was written,
        False if skipped as a duplicate (see below). Raises DatabaseError on any
        other failure.

        Preserves the existing status on a re-enrich of an already-known contact —
        without this, re-running enrichment could reset an already-'contacted'
        contact back to 'new', silently dropping them out of Day-3/7 follow-up
        eligibility (same class of bug fixed on LeadRepository.upsert()).

        Duplicate emails (same person surfacing under a different lead/profile_url)
        are rejected atomically by the UNIQUE constraint on email_canonical
        (see migrations/0001_contacts_email_canonical.sql) rather than a
        check-then-insert in Python, which would race under concurrent writers.
        Postgres rolls the whole statement back on a constraint violation, so a
        rejected write never partially applies.
        """
        try:
            data = contact.to_db_dict()
            existing = (
                self._client.table(self._table)
                .select("id")
                .eq("profile_url", contact.profile_url)
                .limit(1)
                .execute()
            )
            if existing.data:
                data.pop("status", None)
            self._client.table(self._table).upsert(
                data,
                on_conflict="profile_url",
            ).execute()
            return True
        except APIError as exc:
            error_text = (exc.message or "") + (exc.details or "")
            if exc.code == _UNIQUE_VIOLATION and "email_canonical" in error_text:
                logger.info(
                    "Duplicate contact skipped - email already in DB | email=%s | name=%s",
                    contact.email, contact.name,
                )
                return False
            logger.error("contacts upsert failed for '%s': %s", contact.name, exc)
            raise DatabaseError(f"contacts upsert failed: {exc}") from exc
        except Exception as exc:
            logger.error("contacts upsert failed for '%s': %s", contact.name, exc)
            raise DatabaseError(f"contacts upsert failed: {exc}") from exc

    def update_status(self, contact_id: str, status: str) -> bool:
        try:
            self._client.table(self._table).update({"status": status}).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_status failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_status failed: {exc}") from exc

    def update_flag(self, contact_id: str, reason: str, rejected_line: str = "") -> bool:
        """Quarantine a contact whose AI-drafted content failed grounding validation
        even after retry. status='flagged' excludes it from get_unpersonalized() and
        get_personalized_unsent(), so it can never reach the normal review/send queue.
        rejected_line (if given) is stored in personalized_line anyway, purely so a
        human can inspect what was rejected and why on the /flagged screen."""
        try:
            payload: dict = {"status": "flagged", "flag_reason": reason}
            if rejected_line:
                payload["personalized_line"] = rejected_line
            self._client.table(self._table).update(payload).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_flag failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_flag failed: {exc}") from exc

    def reset_to_new(self, contact_id: str) -> bool:
        """Clear a flagged contact back to status='new' with no personalized_line/
        flag_reason, so the next Generate Pitches run picks it up and retries it
        fresh - used by the /flagged screen's Retry action."""
        try:
            self._client.table(self._table).update(
                {"status": "new", "personalized_line": None, "flag_reason": None}
            ).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("reset_to_new failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"reset_to_new failed: {exc}") from exc

    def get_flagged(self, limit: int) -> list[dict]:
        """Return contacts quarantined for failing grounding validation, oldest first."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id, personalized_line, flag_reason")
                .eq("status", "flagged")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_flagged failed: %s", exc)
            raise DatabaseError(f"get_flagged failed: {exc}") from exc

    def update_personalized_line(self, contact_id: str, line: str) -> bool:
        """Save GPT opening line and advance status to 'personalized'."""
        try:
            self._client.table(self._table).update(
                {"personalized_line": line, "status": "personalized"}
            ).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_personalized_line failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_personalized_line failed: {exc}") from exc

    def update_email_sent(self, contact_id: str, email_sent_at: str) -> bool:
        """Record Day-1 send timestamp and advance status to 'contacted'."""
        try:
            self._client.table(self._table).update({
                "email_sent_at": email_sent_at,
                "last_contacted": email_sent_at,
                "status": "contacted",
            }).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_email_sent failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_email_sent failed: {exc}") from exc

    def update_last_contacted(self, contact_id: str) -> bool:
        """Stamp last_contacted = now (called after Day-3 and Day-7 sends)."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            self._client.table(self._table).update(
                {"last_contacted": now_iso}
            ).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_last_contacted failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_last_contacted failed: {exc}") from exc

    def update_reply(self, contact_id: str, classification: str) -> bool:
        """Record reply classification and advance status to terminal state."""
        status = _CONTACT_REPLY_STATUS.get(classification, "replied")
        try:
            self._client.table(self._table).update({
                "reply_classification": classification,
                "status": status,
            }).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_reply failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_reply failed: {exc}") from exc

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_created_since(self, iso_timestamp: str) -> list[dict]:
        """Return contacts scraped at or after the given ISO timestamp — used to show
        exactly which contacts a single scrape run just found."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id")
                .gte("scraped_at", iso_timestamp)
                .order("scraped_at", desc=False)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_created_since failed: %s", exc)
            raise DatabaseError(f"get_created_since failed: {exc}") from exc

    def get_unpersonalized(self, limit: int) -> list[dict]:
        """Return new contacts that have an email but no opening line yet."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id")
                .eq("status", "new")
                .not_.is_("email", "null")
                .is_("personalized_line", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_unpersonalized failed: %s", exc)
            raise DatabaseError(f"get_unpersonalized failed: {exc}") from exc

    def get_personalized_unsent(self, limit: int) -> list[dict]:
        """Return contacts ready to email — personalized but not yet contacted."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id, personalized_line")
                .eq("status", "personalized")
                .is_("email_sent_at", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_personalized_unsent failed: %s", exc)
            raise DatabaseError(f"get_personalized_unsent failed: {exc}") from exc

    def get_by_id(self, contact_id: str) -> Optional[dict]:
        """Return a single contact by primary key, or None if not found."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id, personalized_line")
                .eq("id", contact_id)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.error("get_by_id failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"get_by_id failed: {exc}") from exc

    def get_contacted_by_email(self, email: str) -> Optional[dict]:
        """Return a contacted contact matching the given email, or None."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, title, email")
                .eq("email", email)
                .eq("status", "contacted")
                .limit(1)
                .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as exc:
            logger.error("get_contacted_by_email failed for %s: %s", email, exc)
            raise DatabaseError(f"get_contacted_by_email failed: {exc}") from exc

    def get_due_day3(self, limit: int) -> list[dict]:
        """Return contacted contacts whose Day-3 follow-up is due."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id, personalized_line")
                .eq("status", "contacted")
                .lte("email_sent_at", cutoff)
                .lte("last_contacted", cutoff)
                .order("email_sent_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_due_day3 (contacts) failed: %s", exc)
            raise DatabaseError(f"get_due_day3 failed: {exc}") from exc

    def get_due_day7(self, limit: int) -> list[dict]:
        """Return contacted contacts whose Day-7 urgency email is due."""
        try:
            cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cutoff_3d = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            result = (
                self._client.table(self._table)
                .select("id, name, title, email, lead_id, personalized_line")
                .eq("status", "contacted")
                .lte("email_sent_at", cutoff_7d)
                .lte("last_contacted", cutoff_3d)
                .order("email_sent_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_due_day7 (contacts) failed: %s", exc)
            raise DatabaseError(f"get_due_day7 failed: {exc}") from exc

    def get_contacts_without_email(self, limit: int) -> list[dict]:
        """Return new contacts that have no email address yet."""
        try:
            result = (
                self._client.table(self._table)
                .select("id, name, lead_id")
                .eq("status", "new")
                .is_("email", "null")
                .order("scraped_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error("get_contacts_without_email failed: %s", exc)
            raise DatabaseError(f"get_contacts_without_email failed: {exc}") from exc

    def update_email(self, contact_id: str, email: str) -> bool:
        """Set the email address on a contact (status remains 'new')."""
        try:
            self._client.table(self._table).update(
                {"email": email}
            ).eq("id", contact_id).execute()
            return True
        except Exception as exc:
            logger.error("update_email failed for id=%s: %s", contact_id, exc)
            raise DatabaseError(f"update_email failed: {exc}") from exc
