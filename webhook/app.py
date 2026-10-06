"""
Flask webhook app — receives tracking link clicks from cold emails.

Routes:
  GET /health          — health check (Railway uptime probe)
  GET /track?ref=<id>  — fires systeme.io webhook, then redirects lead to CTA

Run locally:
  python webhook/app.py

Deploy on Railway as a separate service from the orchestrator.
Set PORT env var (Railway injects it automatically).
"""
from __future__ import annotations

import os
import sys

import requests
from flask import Flask, Response, jsonify, redirect, request

# Allow running from project root or from within webhook/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from core.logging_config import configure_logging, get_logger

_s = get_settings()
configure_logging(_s.log_level)
logger = get_logger("webhook.app")

app = Flask(__name__)

# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/health")
def health() -> tuple[Response, int]:
    """Railway uptime probe — always returns 200."""
    return jsonify({"status": "ok"}), 200


@app.route("/track")
def track() -> Response:
    """
    Receives a click on the tracking link embedded in outbound emails.
    Fires the systeme.io webhook (if configured), then redirects the
    lead to the CTA booking page.
    """
    ref = request.args.get("ref", "").strip()

    logger.info("Track click received | ref=%s | ip=%s", ref or "(none)", request.remote_addr)

    _fire_systeme_webhook(_s.systeme_io_webhook_url, ref)

    return redirect(_s.email_cta_url, code=302)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fire_systeme_webhook(webhook_url: str, ref: str) -> None:
    """POST the click event to systeme.io. Failure is non-fatal — redirect still happens."""
    if not webhook_url:
        logger.debug("SYSTEME_IO_WEBHOOK_URL not set — skipping webhook fire")
        return
    try:
        resp = requests.post(
            webhook_url,
            json={"ref": ref},
            timeout=5,
        )
        logger.info(
            "systeme.io webhook fired | ref=%s | status=%d", ref, resp.status_code
        )
    except requests.RequestException as exc:
        logger.warning("systeme.io webhook failed | ref=%s | error=%s", ref, exc)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    s = get_settings()
    configure_logging(s.log_level)
    port = int(os.environ.get("PORT", 5000))
    logger.info("Webhook app starting on port %d", port)
    app.run(host="0.0.0.0", port=port)
