from __future__ import annotations

import random
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from config.settings import Settings, get_settings
from core.database import LeadRepository, ScrapingResult
from core.logging_config import get_logger
from core.models import Lead
from core.rate_limiter import SyncRateLimiter
from scrapers.base import BaseScraper

_BASE_URL = "https://www.yellowpages.com/search"
_IMPERSONATE = "firefox147"  # Chrome/Edge TLS signatures are currently Cloudflare-blocked on this site
_CATEGORY_PAUSE_SECONDS = 15  # pause between category searches in the same city to avoid Cloudflare rate limiting
_MAX_FETCH_RETRIES = 3        # retry 403s with backoff before giving up on a page
_TARGET_NEW_LEADS = 25        # stop the whole run once this many NEW leads have been saved

_DEFAULT_CITY = "Richmond"
_DEFAULT_STATE = "VA"

# Broad small-business coverage — intentionally not skewed toward any one vertical.
_SMALL_BUSINESS_CATEGORIES: list[str] = [
    "nail salons",
    "day spas",
    "medical centers",
    "florists",
    "chiropractors",
    "hvac contractors",
    "plumbers",
    "restaurants",
    "auto repair",
    "hair salons",
    "gyms",
    "dentists",
    "law firms",
    "accountants",
    "veterinarians",
    "landscaping",
    "electricians",
    "roofing contractors",
    "pest control",
    "cleaning services",
    "photographers",
    "wedding planners",
    "personal trainers",
    "interior designers",
    "insurance agents",
    "real estate brokers",
    "financial planners",
    "bakeries",
    "coffee shops",
    "boutiques",
]


class YellowPagesScraper(BaseScraper):
    """
    Scrapes Yellow Pages for small businesses across many categories in one
    city, stopping once _TARGET_NEW_LEADS new leads have been saved.
    Uses curl_cffi to bypass Cloudflare via Chrome TLS fingerprint impersonation.
    No browser required — pure HTTP requests.
    """

    source = "yellowpages"

    def __init__(
        self,
        db: LeadRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(db)
        self._s = settings or get_settings()
        self._session = cffi_requests.Session(impersonate=_IMPERSONATE)
        self._rate = SyncRateLimiter(self._s.scraper_delay_min, self._s.scraper_delay_max)

    async def run(self, city: str = _DEFAULT_CITY, state: str = _DEFAULT_STATE) -> list[ScrapingResult]:
        """Scrape one city across small-business categories until _TARGET_NEW_LEADS
        new leads are saved or every category has been tried. Returns a single-item
        list (kept as a list for compatibility with callers that sum across results)."""
        geo = f"{city}, {state}"
        result = ScrapingResult(city=geo)
        categories = list(_SMALL_BUSINESS_CATEGORIES)
        random.shuffle(categories)

        self.logger.info(
            "START city=%s target_new_leads=%d categories=%d",
            geo, _TARGET_NEW_LEADS, len(categories),
        )

        for idx, category in enumerate(categories):
            if idx > 0:
                # Fresh session + pause between category searches prevents Cloudflare rate limiting
                self._session = cffi_requests.Session(impersonate=_IMPERSONATE)
                self.logger.info(
                    "Inter-category pause %ds before '%s' | city=%s",
                    _CATEGORY_PAUSE_SECONDS, category, geo,
                )
                time.sleep(_CATEGORY_PAUSE_SECONDS)

            self._scrape_category(category, geo, result)
            result.categories_tried += 1

            if result.saved >= _TARGET_NEW_LEADS:
                self.logger.info(
                    "Target reached | city=%s | saved=%d/%d | categories_tried=%d",
                    geo, result.saved, _TARGET_NEW_LEADS, result.categories_tried,
                )
                break
        else:
            self.logger.info(
                "Exhausted all categories | city=%s | saved=%d/%d",
                geo, result.saved, _TARGET_NEW_LEADS,
            )

        self.logger.info(
            "DONE city=%s | saved=%d skipped=%d errors=%d categories_tried=%d",
            geo, result.saved, result.skipped, result.errors, result.categories_tried,
        )
        return [result]

    # ── Category-level scrape ─────────────────────────────────────────────────

    def _scrape_category(self, category: str, geo: str, result: ScrapingResult) -> None:
        """Scrape pages for one category in one city, stopping early if the run's
        overall new-lead target is reached mid-page."""
        self.logger.info(
            "Category START '%s' | city=%s | max_pages=%d",
            category, geo, self._s.scraper_max_pages,
        )

        for page_num in range(1, self._s.scraper_max_pages + 1):
            cards = self._fetch_page(geo, category, page_num)
            if not cards:
                self.logger.info(
                    "No results on page %d for '%s' | city=%s — next category",
                    page_num, category, geo,
                )
                break

            for card in cards:
                self._process_card(card, geo, result)
                if result.saved >= _TARGET_NEW_LEADS:
                    return

            self.logger.info(
                "Page %d/%d | category=%s | city=%s | saved=%d skipped=%d errors=%d",
                page_num, self._s.scraper_max_pages, category, geo,
                result.saved, result.skipped, result.errors,
            )

            if page_num < self._s.scraper_max_pages:
                self._rate.wait()

        self.logger.info(
            "Category DONE '%s' | city=%s | saved=%d skipped=%d errors=%d",
            category, geo, result.saved, result.skipped, result.errors,
        )

    def _fetch_page(self, geo: str, category: str, page: int) -> list:
        """Fetch one search results page. Retries on 403 with backoff. Returns BS4 card list."""
        params: dict[str, str] = {
            "search_terms": category,
            "geo_location_terms": geo,
        }
        if page > 1:
            params["page"] = str(page)

        url = f"{_BASE_URL}?{urlencode(params)}"
        self.logger.debug("GET page=%d url=%s", page, url)

        for attempt in range(1, _MAX_FETCH_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=20)
                resp.raise_for_status()
                break
            except Exception as exc:
                if attempt == _MAX_FETCH_RETRIES:
                    self.logger.warning(
                        "Fetch failed after %d attempts | page=%d city=%s | error=%s",
                        _MAX_FETCH_RETRIES, page, geo, exc,
                    )
                    return []
                wait = 10 * attempt  # 10s, 20s backoff
                self.logger.warning(
                    "Fetch error (attempt %d/%d) | page=%d city=%s | retrying in %ds | error=%s",
                    attempt, _MAX_FETCH_RETRIES, page, geo, wait, exc,
                )
                # Fresh session on retry — Cloudflare ties blocks to session state
                self._session = cffi_requests.Session(impersonate=_IMPERSONATE)
                time.sleep(wait)

        soup = BeautifulSoup(resp.text, "html.parser")

        # Prefer organic wrapper; fall back to all results minus promoted listings
        cards = soup.select(".organic .result")
        if not cards:
            cards = soup.select(".result:not(.featured-listing):not(.sponsored-listing)")
        return cards

    # ── Per-card processing ───────────────────────────────────────────────────

    def _process_card(self, card, geo: str, result: ScrapingResult) -> None:
        """Extract fields from one result card, validate, and upsert to DB."""
        try:
            name = self._extract_name(card)
            if not name:
                result.skipped += 1
                return

            phone = self._extract_phone(card)
            if not phone:
                result.skipped += 1
                return

            profile_url = self._extract_profile_url(card)
            if not profile_url:
                result.skipped += 1
                return

            category = self._extract_category(card)
            lead = Lead(
                name=name,
                phone=phone,
                brokerage=name,
                location=self._extract_location(card) or geo,
                website_url=self._extract_website(card),
                profile_url=profile_url,
                source="yellowpages",
                description_raw=category,
                description_source="yellowpages_category" if category else None,
            )

            if not lead.is_contactable:
                # Phone failed Pydantic normalisation (not 10 digits)
                result.skipped += 1
                return

            self.db.upsert(lead)
            result.saved += 1
            self.logger.debug(
                "Saved | name=%s | phone=%s | city=%s", name, lead.phone, geo
            )

        except Exception as exc:
            self.logger.warning("Card failed | city=%s | error=%s", geo, exc)
            result.errors += 1

    # ── Field extractors ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_name(card) -> Optional[str]:
        el = card.select_one(".business-name span") or card.select_one(".business-name")
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _extract_phone(card) -> Optional[str]:
        el = card.select_one(".phones.phone.primary") or card.select_one(".phone")
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _extract_profile_url(card) -> Optional[str]:
        el = card.select_one("a.business-name")
        if not el:
            return None
        href = el.get("href", "")
        if not href:
            return None
        if href.startswith("http"):
            return href
        return urljoin("https://www.yellowpages.com", href)

    @staticmethod
    def _extract_website(card) -> Optional[str]:
        el = card.select_one("a.track-visit-website")
        return el.get("href") if el else None

    @staticmethod
    def _extract_category(card) -> Optional[str]:
        """Yellow Pages' own category text for this listing (e.g. 'Real Estate Agents
        Business Brokers') - stored raw and verbatim, used to ground AI-drafted copy."""
        el = card.select_one(".categories")
        return el.get_text(" ", strip=True) if el else None

    @staticmethod
    def _extract_location(card) -> Optional[str]:
        street = card.select_one(".street-address")
        locality = card.select_one(".locality")
        parts = []
        if street:
            parts.append(street.get_text(strip=True))
        if locality:
            parts.append(locality.get_text(strip=True))
        return ", ".join(parts) if parts else None
