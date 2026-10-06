from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.database import EnrichmentResult, LeadRepository
from core.logging_config import get_logger


class BaseEnricher(ABC):
    """
    Contract every enricher must fulfil.
    Subclasses inject their own settings and db via __init__.
    """

    source: str = ""

    def __init__(self, db: LeadRepository) -> None:
        self.db = db
        self.logger = get_logger(f"enrichers.{self.source or self.__class__.__name__}")

    @abstractmethod
    def enrich_one(
        self,
        name: str,
        brokerage: Optional[str],
        location: Optional[str],
        website_url: Optional[str] = None,
    ) -> Optional[str]:
        """Return an email string, or None if not found."""
        ...

    @abstractmethod
    def run(
        self,
        max_leads: int = 0,
        high_value_only: bool = False,
        min_listings: int = 0,
    ) -> EnrichmentResult:
        """Fetch un-emailed leads from DB, enrich each, write results back. 0 = use settings value."""
        ...
