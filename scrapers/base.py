from __future__ import annotations

from abc import ABC, abstractmethod

from core.database import LeadRepository, ScrapingResult
from core.logging_config import get_logger


class BaseScraper(ABC):
    """
    Contract every scraper must fulfil.
    Subclasses inject their own settings and db via __init__.
    """

    source: str = ""

    def __init__(self, db: LeadRepository) -> None:
        self.db = db
        self.logger = get_logger(f"scrapers.{self.source or self.__class__.__name__}")

    @abstractmethod
    async def run(self) -> list[ScrapingResult]:
        """Execute the full scrape and return per-target results."""
        ...
