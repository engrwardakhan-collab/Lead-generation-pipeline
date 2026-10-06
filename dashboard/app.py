"""
Local review-and-send dashboard — human-in-the-loop control over the pipeline.

Nothing sends automatically. Every outbound email (initial or follow-up) is
generated as a draft, shown one at a time with the lead's full context, and
only sent after an explicit Approve click. Editing + "Polish with AI" let you
rewrite a draft before sending.

Run locally:
    python dashboard/app.py
Then open http://localhost:5000
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from flask import Flask, redirect, render_template, request, url_for

# Allow running from project root or from within dashboard/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.email_agent import EmailAgent
from agents.followup_agent import FollowupAgent
from agents.personalization import PersonalizationAgent, is_generic_fallback
from config.settings import get_settings
from core.brevo_sender import BrevoSender
from core.content_polisher import ContentPolisher
from core.database import ContactRepository, LeadRepository
from core.exceptions import ConvertAIError
from core.logging_config import configure_logging, get_logger
from core.sent_log import SentEmailLog
from dashboard.us_cities import STATE_CITIES, US_STATES

_s = get_settings()
configure_logging(_s.log_level)
logger = get_logger("dashboard.app")

app = Flask(__name__)

_leads = LeadRepository()
_contacts = ContactRepository()
_sender = BrevoSender()
_email_agent = EmailAgent(contacts=_contacts, sender=_sender)
_sent_log = SentEmailLog()
_polisher = ContentPolisher()
_followup_agent = FollowupAgent(sent_log=_sent_log)
_personalization_agent = PersonalizationAgent(contacts=_contacts, db=_leads)

_SCRAPE_ENRICH_BATCH = 25  # matches the batch size used in earlier manual runs
_GENERATE_BATCH = 5  # untouched leads pitched per "Generate Pitches" click

# Per-process, in-memory only. A follow-up "skip" means "not this session" —
# it must NOT change status='contacted' (that drives Day-3/7 eligibility), so
# there's nowhere in the DB to persist it without a schema change. Resets on
# app restart, which is fine: the contact will just be offered again.
_skipped_followups: set[str] = set()


def _first_name(full_name: str) -> str:
    name = (full_name or "").strip()
    return name.split()[0].rstrip(",") if name else name


def _lead_for(contact: dict) -> dict:
    lead_id = contact.get("lead_id")
    return (_leads.get_by_id(lead_id) if lead_id else None) or {}


def _parse_flash_ok(raw: Optional[str]) -> Optional[bool]:
    """Read the 'sent' query param ('1'/'0') into True/False/None (no send just happened)."""
    if raw is None:
        return None
    return raw == "1"


# ── Home ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    pending_drafts = len(_contacts.get_personalized_unsent(limit=200))
    untouched = len(_contacts.get_unpersonalized(limit=200))
    flagged = len(_contacts.get_flagged(limit=200))
    due = {
        c["id"]: c
        for c in (_contacts.get_due_day7(limit=200) + _contacts.get_due_day3(limit=200))
        if c["id"] not in _skipped_followups
    }
    return render_template(
        "index.html",
        pending_drafts=pending_drafts,
        untouched=untouched,
        flagged=flagged,
        pending_followups=len(due),
        sent_count=len(_sent_log.recent(limit=500)),
        batch_size=_SCRAPE_ENRICH_BATCH,
        generate_batch=_GENERATE_BATCH,
        scrape_error=request.args.get("scrape_error"),
        city_value=request.args.get("city", ""),
        state_value=request.args.get("state", ""),
        us_states=US_STATES,
        state_cities=STATE_CITIES,
    )


@app.route("/scrape", methods=["POST"])
def scrape():
    from datetime import datetime, timezone

    from pipeline.runner import _enrich, _scrape

    city = request.form.get("city", "").strip()
    state = request.form.get("state", "").strip().upper()

    # Dropdowns constrain the browser to valid combinations, but a request
    # could still arrive with JS disabled or tampered with - re-check server
    # side against the same allow-list rather than trusting the client.
    if state not in STATE_CITIES or city not in STATE_CITIES[state]:
        return redirect(url_for(
            "index",
            scrape_error="Pick a state, then a city from that state's list.",
            city=city,
            state=state,
        ))

    run_started = datetime.now(timezone.utc).isoformat()
    error = None
    saved = 0
    enrich_result = None

    try:
        scrape_results = _scrape(city=city, state=state)
        saved = sum(r.saved for r in scrape_results)
        categories_tried = scrape_results[0].categories_tried if scrape_results else 0
        enrich_result = _enrich(max_leads=_SCRAPE_ENRICH_BATCH)
        logger.info(
            "Dashboard scrape: city=%s, %s | %d new leads across %d categories | "
            "enrich: leads=%d contacts=%d duplicates=%d errors=%d",
            city, state, saved, categories_tried,
            enrich_result.leads_processed, enrich_result.contacts_saved,
            enrich_result.duplicates_skipped, enrich_result.errors,
        )
    except ConvertAIError as exc:
        logger.error("Scrape/enrich failed: %s", exc)
        error = str(exc)
        categories_tried = 0

    found_contacts = []
    if enrich_result and enrich_result.contacts_saved:
        for c in _contacts.get_created_since(run_started):
            lead = _lead_for(c)
            found_contacts.append({
                "name": c["name"], "title": c.get("title"), "email": c.get("email"),
                "business": lead.get("brokerage") or lead.get("name"),
            })

    return render_template(
        "scrape_result.html",
        error=error,
        city=city,
        state=state,
        leads_saved=saved,
        categories_tried=categories_tried,
        enrich_result=enrich_result,
        found_contacts=found_contacts,
    )


@app.route("/sent")
def sent():
    records = _sent_log.recent(limit=100)
    return render_template("sent.html", records=records)


@app.route("/flagged")
def flagged():
    """Contacts whose AI-drafted content failed grounding validation twice - quarantined
    here instead of the normal review queue. Never auto-sent; a human decides Retry
    (regenerate fresh next Generate Pitches run) or Discard (drop it)."""
    rows = []
    for c in _contacts.get_flagged(limit=100):
        lead = _lead_for(c)
        rows.append({"contact": c, "lead": lead})
    return render_template("flagged.html", rows=rows)


@app.route("/flagged/<contact_id>/retry", methods=["POST"])
def flagged_retry(contact_id):
    _contacts.reset_to_new(contact_id)
    return redirect(url_for("flagged"))


@app.route("/flagged/<contact_id>/discard", methods=["POST"])
def flagged_discard(contact_id):
    _contacts.update_status(contact_id, "skipped")
    return redirect(url_for("flagged"))


@app.route("/generate_review", methods=["POST"])
def generate_review():
    """Top the review queue up to exactly _GENERATE_BATCH contacts, then drop
    into it. Never adds a full new batch on top of what's already pending -
    if leftovers are already sitting in the queue (an interrupted prior batch,
    or any other stray 'personalized' contact), this tops up to 5 total
    instead of adding 5 more, so the queue can never silently exceed 5."""
    already_pending = len(_contacts.get_personalized_unsent(limit=_GENERATE_BATCH))
    to_generate = max(0, _GENERATE_BATCH - already_pending)
    if to_generate:
        try:
            result = _personalization_agent.run(max_leads=to_generate)
            logger.info(
                "Dashboard generate: written=%d fallback=%d flagged=%d errors=%d",
                result.written, result.fallback, result.flagged, result.errors,
            )
        except ConvertAIError as exc:
            logger.error("Generate pitches failed: %s", exc)
    return redirect(url_for("review"))


# ── Day-1 draft review ───────────────────────────────────────────────────────


@app.route("/review")
def review():
    pending = _contacts.get_personalized_unsent(limit=1)
    if not pending:
        return render_template(
            "review.html", empty=True, queue_endpoint="review",
            flash_ok=_parse_flash_ok(request.args.get("sent")),
            flash_name=request.args.get("sent_name"),
        )

    contact = pending[0]
    lead = _lead_for(contact)
    first_name = _first_name(contact["name"])
    subject, body = _email_agent._build_email(
        day=1,
        first_name=first_name,
        personalized_line=contact.get("personalized_line") or "",
        cta_url=_email_agent._tracking_url(contact["id"]),
    )
    return render_template(
        "review.html",
        empty=False,
        queue_endpoint="review",
        day=1,
        contact=contact,
        lead=lead,
        subject=subject,
        body=body,
        fallback_used=is_generic_fallback(contact.get("personalized_line") or ""),
        flash_ok=_parse_flash_ok(request.args.get("sent")),
        flash_name=request.args.get("sent_name"),
    )


@app.route("/review/<contact_id>/approve", methods=["POST"])
def review_approve(contact_id):
    contact = _contacts.get_by_id(contact_id)
    if not contact:
        return redirect(url_for("review"))

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    ok = _email_agent.send_one(contact, subject=subject, body=body, day=1)
    if ok:
        _sent_log.append(
            contact_id=contact["id"], name=contact["name"], email=contact["email"],
            day=1, subject=subject, body=body,
        )
    return redirect(url_for("review", sent=int(ok), sent_name=contact["name"]))


@app.route("/review/<contact_id>/polish", methods=["POST"])
def review_polish(contact_id):
    contact = _contacts.get_by_id(contact_id)
    if not contact:
        return redirect(url_for("review"))

    lead = _lead_for(contact)
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    try:
        subject, body = _polisher.polish(subject, body)
    except ConvertAIError as exc:
        logger.error("Polish failed for %s: %s", contact["name"], exc)

    return render_template(
        "review.html", empty=False, queue_endpoint="review", day=1,
        contact=contact, lead=lead, subject=subject, body=body,
        fallback_used=is_generic_fallback(contact.get("personalized_line") or ""),
    )


@app.route("/review/<contact_id>/skip", methods=["POST"])
def review_skip(contact_id):
    _contacts.update_status(contact_id, "skipped")
    return redirect(url_for("review"))


# ── Day-3/7 follow-up review ─────────────────────────────────────────────────


def _next_followup() -> tuple[dict, int] | tuple[None, None]:
    """Return (contact, day) for the most-overdue eligible follow-up, or (None, None)."""
    for day, fetch in ((7, _contacts.get_due_day7), (3, _contacts.get_due_day3)):
        for contact in fetch(limit=200):
            if contact["id"] not in _skipped_followups:
                return contact, day
    return None, None


@app.route("/followups")
def followups():
    contact, day = _next_followup()
    if not contact:
        return render_template(
            "review.html", empty=True, queue_endpoint="followups",
            flash_ok=_parse_flash_ok(request.args.get("sent")),
            flash_name=request.args.get("sent_name"),
        )

    lead = _lead_for(contact)
    draft = _followup_agent.generate(contact, lead, day)
    if not draft:
        # Can't draft this one (no original send to reference) — skip it for
        # this session so the queue doesn't get stuck on it.
        _skipped_followups.add(contact["id"])
        return redirect(url_for("followups"))

    gpt_subject, gpt_body = draft
    body = _followup_agent.assemble(_first_name(contact["name"]), gpt_body)
    return render_template(
        "review.html",
        empty=False,
        queue_endpoint="followups",
        day=day,
        contact=contact,
        lead=lead,
        subject=gpt_subject,
        body=body,
        flash_ok=_parse_flash_ok(request.args.get("sent")),
        flash_name=request.args.get("sent_name"),
    )


@app.route("/followups/<contact_id>/approve", methods=["POST"])
def followups_approve(contact_id):
    contact = _contacts.get_by_id(contact_id)
    day = int(request.form.get("day", 3))
    if not contact:
        return redirect(url_for("followups"))

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    ok = _email_agent.send_one(contact, subject=subject, body=body, day=day)
    if ok:
        _sent_log.append(
            contact_id=contact["id"], name=contact["name"], email=contact["email"],
            day=day, subject=subject, body=body,
        )
        _skipped_followups.add(contact_id)  # sent — don't re-offer this session
    return redirect(url_for("followups", sent=int(ok), sent_name=contact["name"]))


@app.route("/followups/<contact_id>/polish", methods=["POST"])
def followups_polish(contact_id):
    contact = _contacts.get_by_id(contact_id)
    day = int(request.form.get("day", 3))
    if not contact:
        return redirect(url_for("followups"))

    lead = _lead_for(contact)
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    try:
        subject, body = _polisher.polish(subject, body)
    except ConvertAIError as exc:
        logger.error("Polish failed for %s: %s", contact["name"], exc)

    return render_template(
        "review.html", empty=False, queue_endpoint="followups", day=day,
        contact=contact, lead=lead, subject=subject, body=body,
    )


@app.route("/followups/<contact_id>/skip", methods=["POST"])
def followups_skip(contact_id):
    _skipped_followups.add(contact_id)
    return redirect(url_for("followups"))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Dashboard starting on http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
