"""
ConvertWithAI pipeline entry point.

Usage:
    python pipeline/runner.py scrape      [--city Richmond --state VA]
    python pipeline/runner.py enrich      [--max 10]
    python pipeline/runner.py find_email  [--max 10]
    python pipeline/runner.py personalize [--max 10]
    python pipeline/runner.py email       [--limit 50]
    python pipeline/runner.py replies     [--max 0]
    python pipeline/runner.py all         [--max-enrich 10]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Ensure project root is on path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from core.database import ContactRepository, LeadRepository, ScrapingResult, TeamEnrichmentResult
from core.exceptions import ConfigurationError, ConvertAIError
from core.logging_config import configure_logging, get_logger
from enrichers.email_pattern_enricher import EmailPatternResult


def _scrape(city: str = "", state: str = "") -> list[ScrapingResult]:
    """Scrape one city/state. Omit both to fall back to the scraper's default
    (used by unattended callers like the CLI and the orchestrator)."""
    from scrapers.yellowpages_scraper import YellowPagesScraper
    db = LeadRepository()
    scraper = YellowPagesScraper(db=db)
    if city and state:
        return asyncio.run(scraper.run(city=city, state=state))
    return asyncio.run(scraper.run())


def _enrich(max_leads: int) -> TeamEnrichmentResult:
    from enrichers.team_page_enricher import TeamPageEnricher
    db = LeadRepository()
    contacts = ContactRepository()
    enricher = TeamPageEnricher(db=db, contacts=contacts)
    return enricher.run(max_leads=max_leads)


def _find_email(max_contacts: int) -> EmailPatternResult:
    from enrichers.email_pattern_enricher import EmailPatternEnricher
    db = LeadRepository()
    contacts = ContactRepository()
    enricher = EmailPatternEnricher(db=db, contacts=contacts)
    return enricher.run(max_contacts=max_contacts)


def _personalize(max_leads: int) -> "PersonalizationResult":
    from agents.personalization import PersonalizationAgent, PersonalizationResult
    db = LeadRepository()
    contacts = ContactRepository()
    agent = PersonalizationAgent(contacts=contacts, db=db)
    return agent.run(max_leads=max_leads)


def _email(daily_limit: int) -> "EmailResult":
    from agents.email_agent import EmailAgent, EmailResult
    from core.brevo_sender import BrevoSender
    contacts = ContactRepository()
    sender = BrevoSender()
    agent = EmailAgent(contacts=contacts, sender=sender)
    return agent.run(daily_limit=daily_limit)


def _replies(max_messages: int) -> "ReplyResult":
    from agents.reply_agent import ReplyAgent, ReplyResult
    contacts = ContactRepository()
    agent = ReplyAgent(contacts=contacts)
    return agent.run(max_messages=max_messages)


# ── Sub-command handlers ──────────────────────────────────────────────────────

def cmd_scrape(args: argparse.Namespace, logger) -> None:
    logger.info("=== SCRAPE ===")
    results = _scrape(city=args.city or "", state=args.state or "")
    total = sum(r.saved for r in results)
    logger.info("=== SCRAPE DONE | total saved: %d ===", total)
    for r in results:
        logger.info(
            "  %-20s  saved=%-4d  skipped=%-4d  errors=%-4d  categories_tried=%d",
            r.city, r.saved, r.skipped, r.errors, r.categories_tried,
        )


def cmd_enrich(args: argparse.Namespace, logger) -> None:
    logger.info("=== ENRICH ===")
    result = _enrich(max_leads=args.max)
    logger.info(
        "=== ENRICH DONE | leads=%d | contacts=%d | duplicates=%d | skipped=%d | errors=%d ===",
        result.leads_processed, result.contacts_saved, result.duplicates_skipped,
        result.leads_skipped, result.errors,
    )


def cmd_find_email(args: argparse.Namespace, logger) -> None:
    logger.info("=== FIND_EMAIL ===")
    result = _find_email(max_contacts=args.max)
    logger.info(
        "=== FIND_EMAIL DONE | processed=%d | found=%d | not_found=%d | errors=%d ===",
        result.contacts_processed, result.emails_found, result.not_found, result.errors,
    )


def cmd_personalize(args: argparse.Namespace, logger) -> None:
    logger.info("=== PERSONALIZE ===")
    result = _personalize(max_leads=args.max)
    logger.info(
        "=== PERSONALIZE DONE | written=%d | fallback=%d | flagged=%d | errors=%d ===",
        result.written, result.fallback, result.flagged, result.errors,
    )


def cmd_email(args: argparse.Namespace, logger) -> None:
    logger.info("=== EMAIL ===")
    result = _email(daily_limit=args.limit)
    logger.info(
        "=== EMAIL DONE | day1=%d | day3=%d | day7=%d | errors=%d | total=%d ===",
        result.day1_sent, result.day3_sent, result.day7_sent,
        result.errors, result.total_sent,
    )


def cmd_replies(args: argparse.Namespace, logger) -> None:
    logger.info("=== REPLIES ===")
    result = _replies(max_messages=args.max)
    logger.info(
        "=== REPLIES DONE | processed=%d | interested=%d | not_interested=%d "
        "| unsubscribed=%d | no_match=%d | errors=%d ===",
        result.processed, result.interested, result.not_interested,
        result.unsubscribed, result.no_match, result.errors,
    )


def cmd_all(args: argparse.Namespace, logger) -> None:
    logger.info("=== PIPELINE: scrape → enrich ===")

    logger.info("--- Phase 1: Scrape ---")
    results = _scrape()
    total_scraped = sum(r.saved for r in results)
    logger.info("Scrape complete - %d new leads saved", total_scraped)

    logger.info("--- Phase 2: Enrich ---")
    result = _enrich(max_leads=args.max_enrich)
    logger.info(
        "=== PIPELINE DONE | scraped=%d | contacts=%d | errors=%d ===",
        total_scraped, result.contacts_saved, result.errors,
    )


# ── CLI definition ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runner",
        description="ConvertWithAI lead generation pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scrape
    scrape_p = sub.add_parser("scrape", help="Scrape Yellow Pages small businesses and save to Supabase")
    scrape_p.add_argument("--city", type=str, default="", help="City to search (default: scraper's built-in default)")
    scrape_p.add_argument("--state", type=str, default="", help="2-letter state abbreviation (default: scraper's built-in default)")

    # enrich
    enrich_p = sub.add_parser("enrich", help="Visit brokerage websites, extract decision makers into contacts")
    enrich_p.add_argument(
        "--max", type=int, default=0,
        help="Max leads to process (default: from ENRICHER_MAX_PER_RUN env var, default 50)",
    )

    # find_email
    fe_p = sub.add_parser("find_email", help="Guess + SMTP-verify emails for contacts without one")
    fe_p.add_argument(
        "--max", type=int, default=0,
        help="Max contacts to process (default: from ENRICHER_MAX_PER_RUN env var, default 50)",
    )

    # personalize
    pers_p = sub.add_parser("personalize", help="Generate GPT opening lines for enriched leads")
    pers_p.add_argument(
        "--max", type=int, default=0,
        help="Max leads to personalize (default: from ENRICHER_MAX_PER_RUN env var, default 50)",
    )

    # email
    email_p = sub.add_parser("email", help="Send Day-1/3/7 sequence emails via Brevo")
    email_p.add_argument(
        "--limit", type=int, default=0,
        help="Max emails to send today (default: from EMAIL_DAILY_LIMIT env var, default 50)",
    )

    # replies
    replies_p = sub.add_parser("replies", help="Check inbox and classify replies")
    replies_p.add_argument(
        "--max", type=int, default=0,
        help="Max messages to process per run (default: all unread)",
    )

    # all
    all_p = sub.add_parser("all", help="Run full pipeline: scrape then enrich")
    all_p.add_argument(
        "--max-enrich", type=int, default=0,
        help="Max leads to enrich after scraping (default: ENRICHER_MAX_PER_RUN)",
    )

    return parser


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    logger = get_logger("pipeline.runner")

    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "scrape":
            cmd_scrape(args, logger)
        elif args.command == "enrich":
            cmd_enrich(args, logger)
        elif args.command == "find_email":
            cmd_find_email(args, logger)
        elif args.command == "personalize":
            cmd_personalize(args, logger)
        elif args.command == "email":
            cmd_email(args, logger)
        elif args.command == "replies":
            cmd_replies(args, logger)
        elif args.command == "all":
            cmd_all(args, logger)
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)
    except ConvertAIError as exc:
        logger.error("Pipeline error: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
