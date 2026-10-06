from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError

from config.settings import Settings, get_settings
from core.database import EnrichmentResult, LeadRepository
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger
from core.rate_limiter import SyncRateLimiter
from core.smtp_verifier import SmtpVerifier
from enrichers.base import BaseEnricher

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_CONTACT_KEYWORDS = ("contact", "about", "reach", "connect", "get-in-touch")
_PLATFORM_DOMAINS = frozenset({
    "realtor.com", "zillow.com", "trulia.com", "homes.com", "redfin.com",
    "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "yelp.com", "yellowpages.com", "localsearch.com",
})
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_GPT_SYSTEM = (
    "You are an email extraction assistant. "
    "Extract the single most relevant contact email address from the provided webpage text. "
    "Return only the email address and nothing else. "
    "If no valid email address is present, return exactly: none"
)


class WebsiteEnricher(BaseEnricher):
    """
    Enriches leads by visiting each agent's personal website, finding the
    contact/about page, asking GPT-4o-mini to extract the email, then
    confirming deliverability via SMTP before saving.
    """

    source = "website"

    def __init__(
        self,
        db: LeadRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(db)
        self._s = settings or get_settings()
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for WebsiteEnricher")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)
        self._verifier = SmtpVerifier()
        self._rate = SyncRateLimiter(
            max(0.5, self._s.enricher_delay_seconds - 1.0),
            self._s.enricher_delay_seconds + 1.0,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def enrich_one(
        self,
        name: str,
        brokerage: Optional[str],
        location: Optional[str],
        website_url: Optional[str] = None,
    ) -> Optional[str]:
        if not website_url or self._is_platform_url(website_url):
            return None

        html, homepage_text = self._fetch_page(website_url)
        if html is None:
            raise EnrichmentError(f"Could not fetch website for {name}: {website_url}")

        contact_url = self._find_contact_url(html, website_url)
        if contact_url:
            _, contact_text = self._fetch_page(contact_url)
            content = contact_text or homepage_text
        else:
            content = homepage_text

        if not content:
            return None

        email = self._extract_email_gpt(content)  # raises RateLimitError on 429
        if not email:
            return None

        if not self._verifier.verify(email):
            self.logger.debug("SMTP verification failed for %s (%s)", email, name)
            return None

        return email

    def run(
        self,
        max_leads: int = 0,
        high_value_only: bool = False,
        min_listings: int = 0,
    ) -> EnrichmentResult:
        budget = max_leads or self._s.enricher_max_per_run
        min_lst = min_listings or self._s.enricher_min_listings

        if high_value_only:
            leads = self.db.get_high_value_unenriched(min_listings=min_lst, limit=budget)
        else:
            leads = self.db.get_unenriched(limit=budget)

        # Skip leads whose website_url is missing or points to a platform
        leads = [
            lead for lead in leads
            if lead.get("website_url") and not self._is_platform_url(lead["website_url"])
        ]

        result = EnrichmentResult()
        self.logger.info(
            "WebsiteEnricher START - budget=%d high_value=%s leads_with_website=%d",
            budget, high_value_only, len(leads),
        )

        for idx, lead in enumerate(leads, 1):
            self.logger.debug("[%d/%d] %s - %s", idx, len(leads), lead["name"], lead.get("website_url", ""))
            try:
                email = self.enrich_one(
                    lead["name"],
                    lead.get("brokerage"),
                    lead.get("location"),
                    lead.get("website_url"),
                )
                if email:
                    self.db.mark_enriched(lead["id"], email)
                    result.enriched += 1
                    self.logger.info("  [OK] %s → %s", lead["name"], email)
                else:
                    result.not_found += 1
                    self.logger.info("  -- Not found: %s", lead["name"])
            except RateLimitError:
                self.logger.error("OpenAI rate limit reached - stopping enrichment run")
                break
            except Exception as exc:
                self.logger.error("  [ERR] Error for %s: %s", lead["name"], exc)
                result.errors += 1

            self._rate.wait()

        self.logger.info(
            "WebsiteEnricher DONE - enriched=%d not_found=%d errors=%d",
            result.enriched, result.not_found, result.errors,
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_page(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch URL and return (raw_html, clean_text). Returns (None, None) on failure."""
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)[:6000]
            return resp.text, text
        except Exception as exc:
            self.logger.debug("Fetch failed for %s: %s", url, exc)
            return None, None

    def _find_contact_url(self, html: str, base_url: str) -> Optional[str]:
        """Scan page links for a contact/about page on the same domain."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            base_domain = urlparse(base_url).netloc
            for a in soup.find_all("a", href=True):
                href_lower = a["href"].lower()
                text_lower = (a.get_text() or "").lower()
                if any(kw in href_lower or kw in text_lower for kw in _CONTACT_KEYWORDS):
                    full = urljoin(base_url, a["href"])
                    if urlparse(full).netloc == base_domain:
                        return full
        except Exception as exc:
            self.logger.debug("Contact URL scan failed for %s: %s", base_url, exc)
        return None

    def _extract_email_gpt(self, content: str) -> Optional[str]:
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _GPT_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=50,
                temperature=0,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip().lower()
        if not raw or raw == "none":
            return None
        m = _EMAIL_RE.search(raw)
        if m and "***" not in raw:
            return m.group(0)
        return None

    @staticmethod
    def _is_platform_url(url: str) -> bool:
        """Return True if the URL belongs to a real estate platform, not the agent's own site."""
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
            return any(domain == p or domain.endswith(f".{p}") for p in _PLATFORM_DOMAINS)
        except Exception:
            return True  # Malformed URL - skip
