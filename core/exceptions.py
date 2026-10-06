from __future__ import annotations


class ConvertAIError(Exception):
    """Base for all pipeline errors."""


class ConfigurationError(ConvertAIError):
    """Required config is missing or invalid."""


class DatabaseError(ConvertAIError):
    """A Supabase operation failed."""


class ScraperError(ConvertAIError):
    """A scraping operation failed."""


class EnrichmentError(ConvertAIError):
    """An enrichment operation failed."""


class RateLimitError(EnrichmentError):
    """External API returned 429 and retries were exhausted."""
