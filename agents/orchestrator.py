"""
Master daily orchestrator for the ConvertWithAI pipeline.

Schedule:
  06:00 UTC daily  -- full pipeline: scrape → enrich → personalize → email
  Every 30 minutes - reply inbox check + Telegram alerts

Run:
  python agents/orchestrator.py

Deploy on Railway as a separate service from the webhook app.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from core.exceptions import ConfigurationError
from core.logging_config import configure_logging, get_logger

logger = get_logger("agents.orchestrator")

_PIPELINE_HOUR_UTC = 6
_REPLY_INTERVAL_MIN = 30


# ── Stage runner ──────────────────────────────────────────────────────────────


def _run_stage(name: str, fn: Callable[[], None]) -> bool:
    """Run a pipeline stage, catch any exception, return True on success."""
    logger.info("--- %s START ---", name)
    try:
        fn()
        logger.info("--- %s DONE ---", name)
        return True
    except Exception as exc:
        logger.error("--- %s FAILED: %s ---", name, exc)
        return False


# ── Scheduled jobs ────────────────────────────────────────────────────────────


def daily_pipeline() -> None:
    """
    Runs all four pipeline stages in sequence once per day.
    Each stage is independent - a failure skips that stage but continues the rest.
    """
    from agents.email_agent import EmailAgent
    from agents.personalization import PersonalizationAgent
    from core.brevo_sender import BrevoSender
    from core.database import ContactRepository, LeadRepository
    from enrichers.team_page_enricher import TeamPageEnricher
    from scrapers.yellowpages_scraper import YellowPagesScraper

    logger.info("========== DAILY PIPELINE START ==========")
    db = LeadRepository()
    contacts = ContactRepository()

    def scrape() -> None:
        scraper = YellowPagesScraper(db=db)
        results = asyncio.run(scraper.run())  # uses the scraper's built-in default city/state
        result = results[0]
        logger.info(
            "Scrape: %d new leads saved | city=%s | categories_tried=%d",
            result.saved, result.city, result.categories_tried,
        )

    def enrich() -> None:
        enricher = TeamPageEnricher(db=db, contacts=contacts)
        r = enricher.run()
        logger.info(
            "Enrich: leads=%d contacts=%d duplicates=%d skipped=%d errors=%d",
            r.leads_processed, r.contacts_saved, r.duplicates_skipped, r.leads_skipped, r.errors,
        )

    def find_email() -> None:
        from enrichers.email_pattern_enricher import EmailPatternEnricher
        enricher = EmailPatternEnricher(db=db, contacts=contacts)
        r = enricher.run()
        logger.info(
            "FindEmail: processed=%d found=%d not_found=%d errors=%d",
            r.contacts_processed, r.emails_found, r.not_found, r.errors,
        )

    def personalize() -> None:
        agent = PersonalizationAgent(contacts=contacts, db=db)
        r = agent.run()
        logger.info(
            "Personalize: written=%d fallback=%d flagged=%d errors=%d",
            r.written, r.fallback, r.flagged, r.errors,
        )

    def email() -> None:
        sender = BrevoSender()
        agent = EmailAgent(contacts=contacts, sender=sender)
        r = agent.run()
        logger.info(
            "Email: day1=%d day3=%d day7=%d errors=%d total=%d",
            r.day1_sent, r.day3_sent, r.day7_sent, r.errors, r.total_sent,
        )

    _run_stage("SCRAPE", scrape)
    _run_stage("ENRICH", enrich)
    _run_stage("FIND_EMAIL", find_email)
    _run_stage("PERSONALIZE", personalize)
    _run_stage("EMAIL", email)

    logger.info("========== DAILY PIPELINE DONE ==========")


def check_replies() -> None:
    """Check the IMAP inbox for new replies and classify them."""
    from agents.reply_agent import ReplyAgent
    from core.database import ContactRepository

    contacts = ContactRepository()

    try:
        agent = ReplyAgent(contacts=contacts)
        r = agent.run()
        if r.processed:
            logger.info(
                "Replies: processed=%d interested=%d not_interested=%d unsubscribed=%d errors=%d",
                r.processed, r.interested, r.not_interested, r.unsubscribed, r.errors,
            )
    except ConfigurationError as exc:
        logger.warning("Reply check skipped - IMAP not configured: %s", exc)
    except Exception as exc:
        logger.error("Reply check failed: %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)

    logger.info(
        "Orchestrator starting - daily pipeline at %02d:00 UTC | reply check every %d min",
        _PIPELINE_HOUR_UTC,
        _REPLY_INTERVAL_MIN,
    )

    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        daily_pipeline,
        trigger="cron",
        hour=_PIPELINE_HOUR_UTC,
        minute=0,
        id="daily_pipeline",
        name="Daily pipeline - scrape → enrich → personalize → email",
        misfire_grace_time=3_600,   # run even if scheduler was down, up to 1 hour late
    )

    scheduler.add_job(
        check_replies,
        trigger="interval",
        minutes=_REPLY_INTERVAL_MIN,
        id="check_replies",
        name="Reply inbox check",
    )

    logger.info("Scheduler running. Press Ctrl-C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped.")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
