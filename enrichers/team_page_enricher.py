from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from openai import APIError, APITimeoutError, OpenAI, RateLimitError as OpenAIRateLimitError

from config.settings import Settings, get_settings
from core.database import ContactRepository, LeadRepository, TeamEnrichmentResult
from core.exceptions import ConfigurationError, EnrichmentError, RateLimitError
from core.logging_config import get_logger
from core.models import Contact
from core.rate_limiter import SyncRateLimiter

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_TEAM_KEYWORDS = ("team", "about", "agents", "staff", "people", "meet", "brokers", "our-team")

_PLATFORM_DOMAINS = frozenset({
    "realtor.com", "zillow.com", "trulia.com", "homes.com", "redfin.com",
    "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "yelp.com", "yellowpages.com", "localsearch.com",
})

_DECISION_MAKER_TITLES = frozenset({
    "broker", "owner", "principal", "ceo", "team lead", "team leader",
    "managing broker", "managing director", "director", "partner",
    "president", "founder", "co-founder", "vice president",
})

_GPT_SYSTEM = """\
You extract real estate decision makers from webpage text.
Decision makers are people with titles like: Broker, Owner, Principal, CEO, \
Team Lead, Team Leader, Managing Broker, Director, Partner, President, Founder.

Return a JSON array. Each element must have exactly these keys:
  "name"  -- full name (string, required)
  "title" - their role/title (string, required)
  "email" - email address if visible on the page (string or null)

Return ONLY the JSON array. No markdown, no explanation.
If no decision makers are found, return: []"""


class TeamPageEnricher:
    """
    Visits each brokerage website, finds the team/about page,
    extracts decision makers via GPT-4o-mini, and saves them
    to the contacts table. Marks the lead as 'scraped' when done.
    """

    def __init__(
        self,
        db: LeadRepository,
        contacts: ContactRepository,
        settings: Optional[Settings] = None,
    ) -> None:
        self.db = db
        self._contacts = contacts
        self.logger = get_logger("enrichers.team_page")
        self._s = settings or get_settings()
        if not self._s.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required for TeamPageEnricher")
        self._gpt = OpenAI(api_key=self._s.openai_api_key)
        self._rate = SyncRateLimiter(self._s.enricher_delay_seconds, self._s.enricher_delay_seconds + 2.0)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, max_leads: int = 0) -> TeamEnrichmentResult:
        """Process brokerage leads and extract decision makers into contacts."""
        budget = max_leads or self._s.enricher_max_per_run
        leads = self.db.get_leads_for_team_scrape(limit=budget)

        # Filter out platform/YP-hosted websites before hitting the network
        leads = [
            lead for lead in leads
            if lead.get("website_url") and not self._is_platform_url(lead["website_url"])
        ]

        result = TeamEnrichmentResult()
        self.logger.info("TeamPageEnricher START - budget=%d leads_with_website=%d", budget, len(leads))

        for idx, lead in enumerate(leads, 1):
            self.logger.debug("[%d/%d] %s - %s", idx, len(leads), lead["name"], lead["website_url"])
            try:
                contacts_found = self._process_lead(lead, result)
                result.leads_processed += 1
                result.contacts_saved += contacts_found
                if contacts_found == 0:
                    result.leads_skipped += 1
                self.db.update_status(lead["id"], "scraped")
            except RateLimitError:
                self.logger.error("OpenAI rate limit reached - stopping enrichment run")
                break
            except Exception as exc:
                self.logger.error("Lead failed | name=%s | error=%s", lead["name"], exc)
                result.errors += 1

            self._rate.wait()

        self.logger.info(
            "TeamPageEnricher DONE - leads=%d contacts=%d duplicates=%d skipped=%d errors=%d",
            result.leads_processed, result.contacts_saved, result.duplicates_skipped,
            result.leads_skipped, result.errors,
        )
        return result

    # ── Per-lead processing ───────────────────────────────────────────────────

    def _process_lead(self, lead: dict, result: TeamEnrichmentResult) -> int:
        """Visit brokerage website, extract decision makers, and save new contacts.

        Duplicate verified emails (already present under a different contact) are
        rejected atomically by ContactRepository.upsert()'s UNIQUE constraint on
        email_canonical and counted on `result.duplicates_skipped` rather than
        written. Returns the count of contacts newly saved (excludes duplicates)."""
        website_url = lead["website_url"]

        html, page_text = self._fetch_page(website_url)
        if html is None:
            self.logger.debug("Could not fetch %s", website_url)
            return 0

        # Try team/about page first; fall back to homepage text
        team_url = self._find_team_url(html, website_url)
        if team_url:
            _, team_text = self._fetch_page(team_url)
            content = team_text or page_text
        else:
            content = page_text

        if not content:
            return 0

        people = self._extract_people_gpt(content)
        if not people:
            self.logger.debug("No decision makers found on %s", website_url)
            return 0

        saved = 0
        for person in people:
            contact = self._build_contact(person, lead)
            if contact is None:
                continue

            try:
                was_saved = self._contacts.upsert(contact)
            except Exception as exc:
                self.logger.warning("  [ERR] Contact upsert failed | name=%s | error=%s", contact.name, exc)
                continue

            if was_saved:
                saved += 1
                self.logger.info(
                    "  [OK] Contact saved | name=%s | title=%s | email=%s",
                    contact.name, contact.title, contact.email,
                )
            else:
                result.duplicates_skipped += 1
                self.logger.info(
                    "  [DUP] Contact skipped - email already in DB | name=%s | email=%s",
                    contact.name, contact.email,
                )

        return saved

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch_page(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch URL and return (raw_html, clean_text). Returns (None, None) on failure."""
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)[:8000]
            return resp.text, text
        except Exception as exc:
            self.logger.debug("Fetch failed for %s: %s", url, exc)
            return None, None

    def _find_team_url(self, html: str, base_url: str) -> Optional[str]:
        """Scan page links for a team/about/agents page on the same domain."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            base_domain = urlparse(base_url).netloc
            for a in soup.find_all("a", href=True):
                href_lower = a["href"].lower()
                text_lower = (a.get_text() or "").lower()
                if any(kw in href_lower or kw in text_lower for kw in _TEAM_KEYWORDS):
                    full = urljoin(base_url, a["href"])
                    if urlparse(full).netloc == base_domain:
                        return full
        except Exception as exc:
            self.logger.debug("Team URL scan failed for %s: %s", base_url, exc)
        return None

    def _extract_people_gpt(self, content: str) -> list[dict]:
        """Ask GPT-4o-mini to extract decision makers from page text. Returns list of dicts."""
        try:
            resp = self._gpt.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _GPT_SYSTEM},
                    {"role": "user", "content": content},
                ],
                max_tokens=500,
                temperature=0,
            )
        except OpenAIRateLimitError as exc:
            raise RateLimitError(f"OpenAI rate limit: {exc}") from exc
        except (APIError, APITimeoutError) as exc:
            raise EnrichmentError(f"OpenAI API error: {exc}") from exc

        raw = (resp.choices[0].message.content or "").strip()

        # Strip markdown code fences if GPT wraps the JSON
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            people = json.loads(raw)
            if not isinstance(people, list):
                return []
            return [p for p in people if isinstance(p, dict) and p.get("name") and p.get("title")]
        except json.JSONDecodeError:
            self.logger.debug("GPT returned non-JSON: %s", raw[:200])
            return []

    def _build_contact(self, person: dict, lead: dict) -> Optional[Contact]:
        """Build a validated Contact from GPT output. Returns None if title is not a DM role."""
        title = str(person.get("title", "")).strip()
        name = str(person.get("name", "")).strip()
        email = person.get("email")

        if not self._is_decision_maker(title):
            return None

        # Stable unique key: YP listing URL + name slug
        name_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        profile_url = f"{lead['profile_url']}#{name_slug}"

        try:
            return Contact(
                lead_id=lead["id"],
                name=name,
                title=title,
                email=email,
                profile_url=profile_url,
            )
        except Exception as exc:
            self.logger.debug("Contact validation failed for %s: %s", name, exc)
            return None

    @staticmethod
    def _is_decision_maker(title: str) -> bool:
        title_lower = title.lower()
        return any(kw in title_lower for kw in _DECISION_MAKER_TITLES)

    @staticmethod
    def _is_platform_url(url: str) -> bool:
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
            return any(domain == p or domain.endswith(f".{p}") for p in _PLATFORM_DOMAINS)
        except Exception:
            return True
