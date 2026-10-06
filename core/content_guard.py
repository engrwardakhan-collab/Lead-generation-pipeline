from __future__ import annotations

from typing import Optional

# Terms that must never appear in AI-drafted outreach copy unless the lead's own
# record explicitly supports them. None of our lead/contact data currently
# carries a verified industry/vertical field, so a hit here always means the
# AI invented an industry claim with no data behind it.
_INDUSTRY_TERMS = (
    "real estate", "realtor", "listing", "listings", "brokerage",
    "properties", "property", "homebuyer", "homebuyers",
)

_COMPANY_SUFFIXES = (" llc", " inc", " inc.", " corp", " corp.", " co", " co.", " ltd", " ltd.", " plc")


def find_unsupported_industry_terms(text: str) -> list[str]:
    """Return which blocklisted industry-specific terms appear in text (case-insensitive)."""
    lowered = text.lower()
    return [term for term in _INDUSTRY_TERMS if term in lowered]


def terms_not_grounded(text: str, description_raw: Optional[str]) -> list[str]:
    """Return which blocklisted industry-cluster terms appear in `text` but are NOT
    present in `description_raw` (the lead's real scraped description). A term found
    in both is fine - it's grounded in the actual source, not invented. If there's no
    description at all, any hit is automatically ungrounded."""
    hits = find_unsupported_industry_terms(text)
    if not hits:
        return []
    lowered_source = (description_raw or "").lower()
    return [term for term in hits if term not in lowered_source]


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


def echoed_text_is_grounded(echoed: Optional[str], description_raw: Optional[str]) -> bool:
    """True if `echoed` (the exact snippet the AI claims it based its line on) is
    actually a verbatim substring of `description_raw` (case/whitespace-insensitive).
    An empty echo, or no description to check against, is never grounded."""
    if not echoed or not echoed.strip():
        return False
    if not description_raw or not description_raw.strip():
        return False
    return _normalize_ws(echoed).lower() in _normalize_ws(description_raw).lower()


def _normalize_company(name: str) -> str:
    s = name.lower().strip().rstrip(".,")
    for suffix in _COMPANY_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def company_mismatch(claimed: Optional[str], actual: Optional[str]) -> bool:
    """True if the company the AI says it referenced doesn't match the lead's real
    company on record. No claim at all (claimed is empty/None) is never a mismatch."""
    if not claimed or not claimed.strip():
        return False
    if not actual or not actual.strip():
        return True  # claimed a company but we have none on record to back it up
    a, b = _normalize_company(claimed), _normalize_company(actual)
    if not a:
        return False
    return a not in b and b not in a
