from __future__ import annotations

from core.content_guard import (
    company_mismatch,
    echoed_text_is_grounded,
    find_unsupported_industry_terms,
    terms_not_grounded,
)


def test_finds_real_estate_term_in_hallucinated_claim():
    # This is the exact real-world failure: an AI-drafted opener claimed a CPA
    # firm was in real estate. The guard must catch it.
    text = "I noticed Metcalf & Company's growth in Dothan's real estate market."
    assert "real estate" in find_unsupported_industry_terms(text)


def test_finds_multiple_blocklisted_terms():
    text = "Your listings and properties in this brokerage are impressive."
    terms = find_unsupported_industry_terms(text)
    assert "listings" in terms
    assert "properties" in terms
    assert "brokerage" in terms


def test_clean_text_has_no_flagged_terms():
    text = "I noticed Metcalf & Company's growth in Dothan. What's eating up your time?"
    assert find_unsupported_industry_terms(text) == []


def test_company_mismatch_flags_unrelated_company():
    assert company_mismatch("Acme Corp", "Metcalf & Company LLC") is True


def test_company_mismatch_allows_exact_match():
    assert company_mismatch("Metcalf & Company LLC", "Metcalf & Company LLC") is False


def test_company_mismatch_allows_suffix_variation():
    # "Metcalf & Company" vs "Metcalf & Company LLC" - same company, just missing the suffix
    assert company_mismatch("Metcalf & Company", "Metcalf & Company LLC") is False


def test_company_mismatch_flags_claim_with_no_company_on_record():
    assert company_mismatch("Some Company", None) is True


def test_no_claim_is_never_a_mismatch():
    assert company_mismatch(None, "Metcalf & Company LLC") is False
    assert company_mismatch("", "Metcalf & Company LLC") is False


# ── terms_not_grounded ──────────────────────────────────────────────────────────

def test_real_estate_term_ungrounded_when_description_is_accounting():
    # The exact real-world failure, now checked against real raw text instead of
    # a blanket ban: a CPA firm's description says nothing about real estate.
    text = "I noticed Metcalf & Company's growth in Dothan's real estate market."
    description = "Accounting Firms"
    assert "real estate" in terms_not_grounded(text, description)


def test_real_estate_term_grounded_when_description_says_real_estate():
    # A genuine real-estate lead (Yellow Pages category = "Real Estate Agents")
    # should NOT be flagged for actually mentioning real estate.
    text = "I noticed your growth in Richmond real estate."
    description = "Real Estate Agents Business Brokers"
    assert terms_not_grounded(text, description) == []


def test_no_description_means_any_hit_is_ungrounded():
    text = "Your listings and properties are impressive."
    # "listing" is itself a substring of "listings", so both blocklist entries hit
    assert terms_not_grounded(text, None) == ["listing", "listings", "properties"]
    assert terms_not_grounded(text, "") == ["listing", "listings", "properties"]


def test_clean_text_has_no_ungrounded_terms():
    text = "I noticed Metcalf & Company's growth in Dothan."
    assert terms_not_grounded(text, "Accounting Firms") == []


# ── echoed_text_is_grounded ──────────────────────────────────────────────────────

def test_echoed_text_found_verbatim_is_grounded():
    assert echoed_text_is_grounded("Real Estate Agents", "Real Estate Agents Business Brokers") is True


def test_echoed_text_is_case_and_whitespace_insensitive():
    assert echoed_text_is_grounded("real   estate agents", "Real Estate Agents Business Brokers") is True


def test_echoed_text_not_in_description_is_ungrounded():
    # This is the "made up a supporting quote" failure mode - the AI claims it
    # echoed something from the description that isn't actually there.
    assert echoed_text_is_grounded("real estate market", "Accounting Firms") is False


def test_empty_echo_is_never_grounded():
    assert echoed_text_is_grounded("", "Accounting Firms") is False
    assert echoed_text_is_grounded(None, "Accounting Firms") is False


def test_echo_with_no_description_is_never_grounded():
    assert echoed_text_is_grounded("Accounting Firms", None) is False
