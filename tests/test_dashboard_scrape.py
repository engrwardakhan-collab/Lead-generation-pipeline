from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy-key")
os.environ.setdefault("BREVO_SMTP_LOGIN", "dummy")
os.environ.setdefault("BREVO_SMTP_KEY", "dummy")
os.environ.setdefault("BREVO_SENDER_EMAIL", "dummy@example.com")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")

import dashboard.app as dashboard_app  # noqa: E402  (env vars must be set first)

# Deterministic fixture - independent of the real curated us_cities.py data,
# so these tests don't break if that list changes.
_FIXTURE_STATE_CITIES = {"VA": ["Richmond", "Norfolk"], "TX": ["Austin", "Houston"]}


def _client():
    dashboard_app.app.testing = True
    return dashboard_app.app.test_client()


def test_missing_city_and_state_redirects_with_error_and_never_scrapes():
    with patch("pipeline.runner._scrape") as mock_scrape, \
         patch.object(dashboard_app, "STATE_CITIES", _FIXTURE_STATE_CITIES):
        resp = _client().post("/scrape", data={"city": "", "state": ""})

    assert resp.status_code == 302
    assert "scrape_error" in resp.headers["Location"]
    mock_scrape.assert_not_called()


def test_unknown_state_redirects_with_error_and_never_scrapes():
    with patch("pipeline.runner._scrape") as mock_scrape, \
         patch.object(dashboard_app, "STATE_CITIES", _FIXTURE_STATE_CITIES):
        resp = _client().post("/scrape", data={"city": "Richmond", "state": "Virginia"})

    assert resp.status_code == 302
    assert "scrape_error" in resp.headers["Location"]
    mock_scrape.assert_not_called()


def test_city_not_belonging_to_state_redirects_with_error_and_never_scrapes():
    """Guards the case where JS is disabled/tampered with and the posted city
    isn't actually one of the selected state's dropdown options."""
    with patch("pipeline.runner._scrape") as mock_scrape, \
         patch.object(dashboard_app, "STATE_CITIES", _FIXTURE_STATE_CITIES):
        resp = _client().post("/scrape", data={"city": "Austin", "state": "VA"})  # Austin is TX, not VA

    assert resp.status_code == 302
    assert "scrape_error" in resp.headers["Location"]
    mock_scrape.assert_not_called()


def test_valid_city_and_state_calls_scrape_with_them():
    with patch("pipeline.runner._scrape") as mock_scrape, \
         patch("pipeline.runner._enrich") as mock_enrich, \
         patch.object(dashboard_app, "STATE_CITIES", _FIXTURE_STATE_CITIES), \
         patch.object(dashboard_app._contacts, "get_created_since", return_value=[]):
        from core.database import ScrapingResult, TeamEnrichmentResult
        mock_scrape.return_value = [ScrapingResult(city="Richmond, VA", saved=3, categories_tried=2)]
        mock_enrich.return_value = TeamEnrichmentResult(leads_processed=3, contacts_saved=1)

        resp = _client().post("/scrape", data={"city": "Richmond", "state": "va"})

    assert resp.status_code == 200
    mock_scrape.assert_called_once_with(city="Richmond", state="VA")
