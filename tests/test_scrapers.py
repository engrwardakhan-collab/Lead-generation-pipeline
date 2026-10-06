from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from core.database import ScrapingResult
from scrapers.yellowpages_scraper import _SMALL_BUSINESS_CATEGORIES, _TARGET_NEW_LEADS, YellowPagesScraper


def _make_scraper() -> YellowPagesScraper:
    settings = MagicMock()
    settings.scraper_delay_min = 0.0
    settings.scraper_delay_max = 0.0
    settings.scraper_max_pages = 5
    with patch("scrapers.yellowpages_scraper.cffi_requests.Session"):
        return YellowPagesScraper(db=MagicMock(), settings=settings)


def test_run_stops_once_target_new_leads_reached_mid_category():
    """_fetch_page returns 10 cards forever and _process_card always saves one
    new lead - the run must stop at exactly _TARGET_NEW_LEADS, breaking out of
    the page loop immediately rather than finishing the current page/category."""
    scraper = _make_scraper()

    def fake_process_card(card, geo, result: ScrapingResult) -> None:
        result.saved += 1

    with patch.object(scraper, "_fetch_page", return_value=list(range(10))), \
         patch.object(scraper, "_process_card", side_effect=fake_process_card) as mock_process:
        results = asyncio.run(scraper.run(city="Richmond", state="VA"))

    assert len(results) == 1
    result = results[0]
    assert result.saved == _TARGET_NEW_LEADS
    assert mock_process.call_count == _TARGET_NEW_LEADS
    # Reached target inside the very first category/page - never needed a second category.
    assert result.categories_tried == 1


def test_run_exhausts_all_categories_when_nothing_found():
    """Every category search returns zero results - the run must try every
    category without erroring and report zero saved."""
    scraper = _make_scraper()

    with patch.object(scraper, "_fetch_page", return_value=[]), \
         patch("scrapers.yellowpages_scraper.time.sleep"):
        results = asyncio.run(scraper.run(city="Nowhere", state="ZZ"))

    result = results[0]
    assert result.saved == 0
    assert result.categories_tried == len(_SMALL_BUSINESS_CATEGORIES)


def test_run_moves_to_next_category_after_empty_page():
    """A category with zero results shouldn't error and should let the run
    continue on to try further categories."""
    scraper = _make_scraper()
    call_count = {"n": 0}

    def fake_fetch_page(geo, category, page):
        call_count["n"] += 1
        return []  # first category (and every category) returns nothing

    with patch.object(scraper, "_fetch_page", side_effect=fake_fetch_page), \
         patch("scrapers.yellowpages_scraper.time.sleep"):
        results = asyncio.run(scraper.run(city="Richmond", state="VA"))

    result = results[0]
    assert result.errors == 0
    assert result.categories_tried == len(_SMALL_BUSINESS_CATEGORIES)
    # Exactly one fetch attempt per category (page 1 comes back empty, so no page 2 attempt).
    assert call_count["n"] == len(_SMALL_BUSINESS_CATEGORIES)
