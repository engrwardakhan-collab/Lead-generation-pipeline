from __future__ import annotations

import asyncio
import random
import re
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

_STEALTH = Stealth()
from pydantic import ValidationError

from config.settings import Settings, get_settings
from core.database import LeadRepository, ScrapingResult
from core.models import Lead
from core.rate_limiter import AsyncRateLimiter
from scrapers.base import BaseScraper

_BASE_URL = "https://www.realtor.com"

_CITIES: list[dict] = [
    {"name": "Miami, FL",   "slug": "Miami_FL"},
    {"name": "Houston, TX", "slug": "Houston_TX"},
    {"name": "Phoenix, AZ", "slug": "Phoenix_AZ"},
    {"name": "Atlanta, GA", "slug": "Atlanta_GA"},
    {"name": "Dallas, TX",  "slug": "Dallas_TX"},
]

_CITY_SLUGS: set[str] = {c["slug"] for c in _CITIES}

_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {}, app: {} };
    const _origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origQuery(p);
"""

_CITY_PAUSE_MIN: float = 4.0
_CITY_PAUSE_MAX: float = 8.0
_PAGE_LOAD_SETTLE: float = 2.5    # seconds after domcontentloaded before extracting
_SCROLL_STEP_PAUSE: float = 0.6   # seconds between scroll steps for lazy-load trigger
_PROFILE_SETTLE: float = 1.8      # seconds after profile page loads before extracting

# Title keywords that indicate a real estate decision maker (broker, owner, team leader, etc.)
# Any profile whose title does NOT contain at least one of these is skipped.
_DECISION_MAKER_KEYWORDS: frozenset[str] = frozenset({
    "broker",
    "owner",
    "principal",
    "ceo",
    "chief executive",
    "president",
    "founder",
    "co-founder",
    "team lead",
    "team leader",
    "team manager",
    "managing",
    "director",
    "partner",
    "vice president",
})


class RealtorScraper(BaseScraper):
    """
    Scrapes Realtor.com for real estate decision makers (brokers, owners, team leaders).
    Only profiles whose title contains a decision-maker keyword are saved.
    """

    source = "realtor"

    def __init__(
        self,
        db: LeadRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(db)
        self._s = settings or get_settings()
        self._rate = AsyncRateLimiter(self._s.scraper_delay_min, self._s.scraper_delay_max)

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self) -> list[ScrapingResult]:
        results: list[ScrapingResult] = []

        self.logger.info(
            "Starting Realtor.com scrape | cities=%d | max_pages=%d | headless=%s",
            len(_CITIES),
            self._s.scraper_max_pages,
            self._s.scraper_headless,
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self._s.scraper_headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                ],
            )
            context = await self._create_context(browser)

            try:
                for city in _CITIES:
                    result = await self._scrape_city(context, city)
                    results.append(result)
                    self.logger.info(
                        "City done: %s — saved=%d skipped=%d errors=%d",
                        city["name"], result.saved, result.skipped, result.errors,
                    )
                    # Brief pause between cities
                    await asyncio.sleep(random.uniform(_CITY_PAUSE_MIN, _CITY_PAUSE_MAX))
            finally:
                await browser.close()

        total = sum(r.saved for r in results)
        self.logger.info("Scrape complete — total leads saved: %d", total)
        return results

    # ── Browser setup ─────────────────────────────────────────────────────────

    async def _create_context(self, browser: Browser) -> BrowserContext:
        context = await browser.new_context(
            user_agent=random.choice(_USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "DNT": "1",
            },
        )
        return context

    async def _new_stealth_page(self, context: BrowserContext) -> Page:
        """Open a new page with full stealth patches applied."""
        page = await context.new_page()
        await _STEALTH.apply_stealth_async(page)
        return page

    # ── City orchestration ────────────────────────────────────────────────────

    async def _scrape_city(self, context: BrowserContext, city: dict) -> ScrapingResult:
        result = ScrapingResult(city=city["name"])
        listing_page = await self._new_stealth_page(context)
        try:
            profile_page = await self._new_stealth_page(context)
        except Exception as exc:
            await listing_page.close()
            self.logger.error("Failed to open profile page for %s: %s", city["name"], exc)
            return result

        try:
            all_urls: list[str] = []
            for page_num in range(1, self._s.scraper_max_pages + 1):
                urls = await self._get_profile_urls(listing_page, city["slug"], page_num)
                if not urls:
                    self.logger.debug("No results on listing page %d for %s", page_num, city["name"])
                    break
                all_urls.extend(urls)
                if page_num < self._s.scraper_max_pages:
                    await self._rate.wait()

            # Deduplicate across pages while preserving order
            seen: set[str] = set()
            unique_urls = [u for u in all_urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

            self.logger.info("%s — %d unique profiles to visit", city["name"], len(unique_urls))

            for idx, url in enumerate(unique_urls, 1):
                self.logger.debug("[%d/%d] %s", idx, len(unique_urls), url)
                try:
                    lead = await self._scrape_profile(profile_page, url, city["name"])
                    if lead is None:
                        result.skipped += 1
                    elif lead.is_contactable:
                        self.db.upsert(lead)
                        result.saved += 1
                        self.logger.info(
                            "  ✓ %s | %s | %s",
                            lead.name,
                            lead.phone,
                            lead.brokerage or "no brokerage",
                        )
                    else:
                        result.skipped += 1
                        self.logger.debug("  — Skipped (no phone): %s", url)
                except Exception as exc:
                    self.logger.warning("  ✗ Error on %s: %s", url, exc)
                    result.errors += 1

                await self._rate.wait()

        finally:
            await listing_page.close()
            await profile_page.close()

        return result

    # ── Listing page: collect agent profile URLs ───────────────────────────────

    async def _get_profile_urls(
        self, page: Page, city_slug: str, page_num: int
    ) -> list[str]:
        path = f"/realestateagents/{city_slug}/"
        if page_num > 1:
            path += f"pg-{page_num}/"
        url = _BASE_URL + path

        self.logger.debug("Listing page: %s", url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(_PAGE_LOAD_SETTLE)
        except Exception as exc:
            self.logger.warning("Listing page load failed: %s", exc)
            return []

        # Scroll to trigger lazy-loaded cards
        for pct in (0.3, 0.6, 1.0):
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
            await asyncio.sleep(_SCROLL_STEP_PAUSE)

        city_slugs_js = list(_CITY_SLUGS)
        profile_urls: list[str] = await page.evaluate(
            """
            (citySlugsList) => {
                const seen    = new Set();
                const result  = [];
                const citySet = new Set(citySlugsList);

                // Strategy 1: links inside recognised card containers
                const cardSelectors = [
                    '[data-testid="agent-list-card"]',
                    '[class*="agent-list-card"]',
                    '[class*="AgentCard"]',
                    '[class*="agent-card"]',
                ];
                let cardLinks = [];
                for (const sel of cardSelectors) {
                    const cards = Array.from(document.querySelectorAll(sel));
                    if (cards.length > 0) {
                        for (const card of cards) {
                            const a = card.querySelector('a[href*="/realestateagents/"]');
                            if (a && a.href) cardLinks.push(a.href);
                        }
                        break;
                    }
                }

                // Strategy 2: fallback — all agent links on page
                if (cardLinks.length === 0) {
                    cardLinks = Array.from(
                        document.querySelectorAll('a[href*="/realestateagents/"]')
                    ).map(a => a.href);
                }

                for (const href of cardLinks) {
                    const clean = href.split('?')[0].replace(/\\/$/, '');
                    const parts = clean
                        .replace(/https?:\\/\\/[^/]+/, '')
                        .split('/')
                        .filter(Boolean);

                    if (parts.length < 2 || parts[0] !== 'realestateagents') continue;

                    const slug = parts[1];
                    if (citySet.has(slug) || /^pg-\\d+$/.test(slug)) continue;

                    // Agent profile slugs have hyphens (First-Last_City_ST_ID);
                    // city search slugs (Miami_FL) do not.
                    if (!slug.includes('-')) continue;

                    const normalized = clean + '/';
                    if (!seen.has(normalized)) {
                        seen.add(normalized);
                        result.push(normalized);
                    }
                }
                return result;
            }
            """,
            city_slugs_js,
        )

        self.logger.debug("Found %d profile URLs on listing page %d", len(profile_urls), page_num)
        return profile_urls

    # ── Profile page: extract one agent ──────────────────────────────────────

    async def _scrape_profile(
        self, page: Page, profile_url: str, city_name: str
    ) -> Optional[Lead]:
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(_PROFILE_SETTLE)
        except Exception as exc:
            self.logger.warning("Profile load error (%s): %s", profile_url, exc)
            return None

        name = await self._safe_text(
            page,
            "h1[data-testid='agent-name']",
            "h1.agent-name",
            "[class*='agentName']",
            "[class*='agent-name']",
            "h1",
        )
        if not name:
            self.logger.debug("No name at %s — skipping", profile_url)
            return None

        # Strip trailing credential labels that appear inside h1 on some profiles
        name = re.sub(r"\s*(REALTOR|Realtor|realtor)[®™]?\s*$", "", name).strip()

        # Filter: only keep decision makers (brokers, owners, team leaders, CEOs, etc.)
        title = await self._extract_title(page)
        if not self._is_decision_maker(title):
            self.logger.debug(
                "Not a decision maker (title=%r) at %s — skipping", title, profile_url
            )
            return None

        phone = await self._extract_phone(page)
        brokerage = await self._safe_text(
            page,
            "[data-testid='agent-broker-name']",
            "[data-testid='agent-office-name']",
            "[data-testid='office-name']",
            "[class*='brokerName']",
            "[class*='broker-name']",
            "[class*='OfficeName']",
            "[class*='office-name']",
        )
        location = await self._safe_text(
            page,
            "[data-testid='agent-location']",
            "[data-testid='agent-address']",
            "[class*='agentLocation']",
            "[class*='agent-location']",
            "[class*='location']",
        ) or city_name

        website_url = await self._extract_website(page)
        listing_count = await self._extract_listing_count(page)

        try:
            return Lead(
                name=name,
                phone=phone,
                brokerage=brokerage,
                location=location,
                website_url=website_url,
                profile_url=profile_url,
                listing_count=listing_count,
                source=self.source,
            )
        except ValidationError as exc:
            self.logger.warning("Lead validation failed for %s: %s", profile_url, exc)
            return None

    # ── Field extractors ──────────────────────────────────────────────────────

    async def _extract_title(self, page: Page) -> Optional[str]:
        """Return the agent's role/designation (e.g. 'Broker/Owner', 'Team Leader')."""
        return await self._safe_text(
            page,
            "[data-testid='agent-title']",
            "[data-testid='agent-type']",
            "[data-testid='agent-designation']",
            "[class*='agentTitle']",
            "[class*='agent-title']",
            "[class*='agentType']",
            "[class*='agentDesignation']",
            "[class*='designation']",
            "[class*='agentLabel']",
        )

    @staticmethod
    def _is_decision_maker(title: Optional[str]) -> bool:
        """True only if the title contains a broker/owner/leadership keyword."""
        if not title:
            return False
        t = title.lower()
        return any(kw in t for kw in _DECISION_MAKER_KEYWORDS)

    async def _extract_phone(self, page: Page) -> Optional[str]:
        try:
            el = await page.query_selector("a[href^='tel:']")
            if el:
                href = (await el.get_attribute("href") or "").replace("tel:", "").replace("+1", "")
                return href or None
        except Exception as exc:
            self.logger.debug("_extract_phone tel: href failed: %s", exc)
        return await self._safe_text(
            page,
            "[data-testid='agent-phone']",
            "[class*='phone']",
            "[class*='Phone']",
        )

    async def _extract_website(self, page: Page) -> Optional[str]:
        _EXCLUDE = {
            "realtor.com", "facebook.com", "linkedin.com", "twitter.com",
            "instagram.com", "zillow.com", "youtube.com", "x.com",
        }
        try:
            links = await page.query_selector_all("a[href^='http']")
            for link in links:
                href = (await link.get_attribute("href") or "").strip()
                if href and not any(d in href for d in _EXCLUDE):
                    return href
        except Exception as exc:
            self.logger.debug("_extract_website link scan failed: %s", exc)
        return None

    async def _extract_listing_count(self, page: Page) -> Optional[int]:
        raw = await self._safe_text(
            page,
            "[data-testid='listing-count']",
            "[class*='listing-count']",
            "[class*='ListingCount']",
            "[class*='active-listing']",
        )
        if raw:
            m = re.search(r"[\d,]+", raw)
            if m:
                return int(m.group().replace(",", ""))

        # Fallback: scan visible text for "N Active Listings" or "N For Sale"
        try:
            body = await page.inner_text("body")
            m = re.search(r"(\d+)\s+(?:Active\s+)?(?:Listing|For Sale)", body)
            if m:
                return int(m.group(1))
        except Exception as exc:
            self.logger.debug("_extract_listing_count body scan failed: %s", exc)
        return None

    # ── DOM helper ────────────────────────────────────────────────────────────

    async def _safe_text(self, root, *selectors: str) -> Optional[str]:
        for sel in selectors:
            try:
                el = await root.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text:
                        return text
            except Exception as exc:
                self.logger.debug("_safe_text selector '%s' failed: %s", sel, exc)
        return None
