from __future__ import annotations

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Single source of truth for every configurable value in the pipeline.
    All values are read from environment variables / .env file.
    Type errors and missing required fields raise at startup, not at runtime.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_key: str
    leads_table: str = "leads"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = ""

    # ── Scraper ───────────────────────────────────────────────────────────────
    scraper_headless: bool = False          # False = visible browser for local testing
    scraper_max_pages: int = Field(default=3, ge=1, le=20)
    scraper_delay_min: float = Field(default=2.0, ge=0.5)
    scraper_delay_max: float = Field(default=5.0, ge=1.0)

    # ── Enricher ──────────────────────────────────────────────────────────────
    enricher_max_per_run: int = Field(default=50, ge=1, le=500)
    enricher_delay_seconds: float = Field(default=3.0, ge=1.0)
    enricher_min_listings: int = Field(default=5, ge=0)     # for --high-value filter

    # ── Email — Brevo SMTP ────────────────────────────────────────────────────
    brevo_smtp_server: str = "smtp-relay.brevo.com"
    brevo_smtp_port: int = 587
    brevo_smtp_login: str = ""
    brevo_smtp_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "Warda Khan"

    # ── Email sending limits + CTA ────────────────────────────────────────────
    email_daily_limit: int = Field(default=50, ge=1, le=300)
    email_cta_url: str = "https://convertwithai.systeme.io"

    # ── Inbox — IMAP (for reply monitoring) ──────────────────────────────────
    imap_server: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""

    # ── Webhook — systeme.io ──────────────────────────────────────────────────
    systeme_io_webhook_url: str = ""
    webhook_base_url: str = ""      # public URL of the Flask webhook app (e.g. https://myapp.railway.app)

    # ── General ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton. Call this everywhere instead of Settings()."""
    return Settings()
