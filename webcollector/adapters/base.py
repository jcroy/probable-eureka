"""Base class for site-specific adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from webcollector.models.crawl_plan import CrawlPlan
from webcollector.models.document import Document


class BaseSiteAdapter(ABC):
    """Override discovery + extraction for a specific site."""

    @abstractmethod
    def matches(self, domain: str) -> bool:
        """Return True if this adapter handles the given domain."""

    @abstractmethod
    async def discover(self, plan: CrawlPlan) -> list[str]:
        """Return seed URLs specific to this site."""

    @abstractmethod
    async def extract(self, url: str, html: str, raw_bytes: bytes) -> list[Document]:
        """Custom extraction logic for this site's pages."""

    def get_rate_limit(self) -> float:
        """Override rate limit for this site (used by per-domain limiter)."""
        return 1.0
