from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.database import TeamEnrichmentResult
from enrichers.team_page_enricher import TeamPageEnricher

_LEAD = {"id": "lead-1", "website_url": "https://example.com", "profile_url": "https://yp/example"}
_PERSON = {"name": "Jane Doe", "title": "Owner", "email": "jane@example.com"}


def _make_enricher() -> TeamPageEnricher:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.enricher_delay_seconds = 0.0
    with patch("enrichers.team_page_enricher.OpenAI"):
        return TeamPageEnricher(db=MagicMock(), contacts=MagicMock(), settings=settings)


def _process(enricher: TeamPageEnricher, upsert_return: bool) -> tuple[int, TeamEnrichmentResult]:
    enricher._contacts.upsert.return_value = upsert_return
    result = TeamEnrichmentResult()
    with patch.object(enricher, "_fetch_page", return_value=("<html></html>", "team page text")), \
         patch.object(enricher, "_find_team_url", return_value=None), \
         patch.object(enricher, "_extract_people_gpt", return_value=[_PERSON]):
        saved = enricher._process_lead(_LEAD, result)
    return saved, result


def test_new_contact_is_saved_and_not_counted_as_duplicate():
    saved, result = _process(_make_enricher(), upsert_return=True)
    assert saved == 1
    assert result.duplicates_skipped == 0
    assert result.errors == 0


def test_duplicate_email_is_skipped_not_saved_and_not_an_error():
    """ContactRepository.upsert() returning False means the DB's UNIQUE
    constraint on email_canonical rejected the write as a duplicate - this
    must be counted separately, never as a save or as an error."""
    saved, result = _process(_make_enricher(), upsert_return=False)
    assert saved == 0
    assert result.duplicates_skipped == 1
    assert result.errors == 0


def test_upsert_exception_is_logged_and_does_not_crash_the_run():
    enricher = _make_enricher()
    enricher._contacts.upsert.side_effect = RuntimeError("connection reset")
    result = TeamEnrichmentResult()

    with patch.object(enricher, "_fetch_page", return_value=("<html></html>", "team page text")), \
         patch.object(enricher, "_find_team_url", return_value=None), \
         patch.object(enricher, "_extract_people_gpt", return_value=[_PERSON]):
        saved = enricher._process_lead(_LEAD, result)

    assert saved == 0
    assert result.duplicates_skipped == 0
