"""
One-off import of a verified-email LinkedIn export (xlsx) into Supabase.

Each unique Company Domain becomes one `leads` row (status='scraped', so
TeamPageEnricher skips it — we already have its decision makers). Each person
row becomes a `contacts` row (status='new') under that lead, ready to flow
straight into PersonalizationAgent → EmailAgent on the next pipeline run.

Usage:
    python pipeline/import_xlsx_leads.py [path/to/file.xlsx]
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Optional

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from core.database import ContactRepository, LeadRepository
from core.exceptions import DatabaseError
from core.logging_config import configure_logging, get_logger
from core.models import Contact, Lead

logger = get_logger("pipeline.import_xlsx_leads")

_DEFAULT_XLSX = Path(__file__).resolve().parent.parent / "Leads_Verified_Emails.xlsx"
_SHEET_NAME = "Verified Emails"
_SOURCE = "linkedin_verified_import"


@dataclasses.dataclass
class ImportResult:
    leads_saved: int = 0
    leads_errors: int = 0
    contacts_saved: int = 0
    contacts_skipped: int = 0
    contacts_errors: int = 0


def _read_rows(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[_SHEET_NAME]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() for h in next(rows)]
    return [dict(zip(header, row)) for row in rows]


def _group_by_domain(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        domain = (row.get("Company Domain") or "").strip().lower()
        if not domain:
            continue
        groups.setdefault(domain, []).append(row)
    return groups


def _dedupe_by_email(rows: list[dict]) -> list[dict]:
    """Keep the first row per work email so the same person isn't emailed twice."""
    seen: set[str] = set()
    deduped = []
    for row in rows:
        email = (row.get("Work Email") or "").strip().lower()
        if email:
            if email in seen:
                continue
            seen.add(email)
        deduped.append(row)
    return deduped


def _import_company(
    domain: str,
    company_rows: list[dict],
    db: LeadRepository,
    contacts: ContactRepository,
    result: ImportResult,
) -> None:
    first = company_rows[0]
    company_name = (first.get("Company Name") or "").strip() or None
    location = (first.get("Location") or "").strip() or None
    headline = (first.get("Headline") or "").strip() or None
    profile_url = f"https://{domain}"

    try:
        lead = Lead(
            name=company_name or domain,
            brokerage=company_name,
            location=location,
            website_url=profile_url,
            profile_url=profile_url,
            source=_SOURCE,
            status="scraped",
            description_raw=headline,
            description_source="linkedin_headline" if headline else None,
        )
        db.upsert(lead)
        lead_id = db.get_id_by_profile_url(profile_url)
    except DatabaseError as exc:
        logger.warning("Lead failed | domain=%s | reason=%s", domain, exc)
        result.leads_errors += 1
        return

    if not lead_id:
        logger.warning("Lead missing after upsert | domain=%s", domain)
        result.leads_errors += 1
        return
    result.leads_saved += 1

    for person in _dedupe_by_email(company_rows):
        name = (person.get("Full Name") or "").strip()
        linkedin_url = person.get("LinkedIn Profile")
        if not name or not linkedin_url:
            result.contacts_skipped += 1
            continue

        try:
            contact = Contact(
                lead_id=lead_id,
                name=name,
                title=(person.get("Job Title") or "").strip() or None,
                email=person.get("Work Email"),
                profile_url=linkedin_url,
                status="new",
            )
            if contacts.upsert(contact):
                result.contacts_saved += 1
                logger.info(
                    "Contact saved | name=%s | email=%s | company=%s",
                    contact.name, contact.email, company_name,
                )
            else:
                result.contacts_skipped += 1
                logger.info(
                    "Duplicate contact skipped | name=%s | email=%s | company=%s",
                    contact.name, contact.email, company_name,
                )
        except DatabaseError as exc:
            logger.warning("Contact failed | name=%s | reason=%s", name, exc)
            result.contacts_errors += 1


def run(path: Optional[Path] = None) -> ImportResult:
    xlsx_path = path or _DEFAULT_XLSX
    rows = _read_rows(xlsx_path)
    groups = _group_by_domain(rows)

    db = LeadRepository()
    contacts = ContactRepository()
    result = ImportResult()

    logger.info(
        "ImportXlsxLeads START | file=%s | companies=%d | rows=%d",
        xlsx_path.name, len(groups), len(rows),
    )

    for domain, company_rows in groups.items():
        _import_company(domain, company_rows, db, contacts, result)

    logger.info(
        "ImportXlsxLeads DONE | leads_saved=%d | leads_errors=%d | "
        "contacts_saved=%d | contacts_skipped=%d | contacts_errors=%d",
        result.leads_saved, result.leads_errors,
        result.contacts_saved, result.contacts_skipped, result.contacts_errors,
    )
    return result


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    arg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run(arg_path)
