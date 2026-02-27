"""HTML content extraction using readability-lxml and BeautifulSoup.

Extracts the main readable content from HTML pages, stripping boilerplate
(nav, footer, ads) while preserving the meaningful article text.
"""

from __future__ import annotations

from typing import Any

import structlog
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDocument

logger = structlog.get_logger(__name__)


class HTMLExtractor:
    """Extract clean text and metadata from HTML content."""

    def extract(self, html: str, url: str = "") -> ExtractionResult:
        """Extract readable text and metadata from raw HTML.

        Uses readability-lxml for main content extraction, then BeautifulSoup
        for text cleanup and metadata parsing.

        Returns an ExtractionResult with the cleaned text and metadata.
        """
        if not html or not html.strip():
            return ExtractionResult(text="", metadata={})

        try:
            # Use readability to find the main content
            doc = ReadabilityDocument(html, url=url)
            title = doc.short_title() or ""
            content_html = doc.summary()

            # Parse the readable content and extract clean text
            soup = BeautifulSoup(content_html, "lxml")
            text = soup.get_text(separator="\n", strip=True)

            # Also parse original HTML for metadata
            original_soup = BeautifulSoup(html, "lxml")
            metadata = self._extract_metadata(original_soup, title)

            return ExtractionResult(
                text=text,
                title=title,
                metadata=metadata,
            )

        except Exception:
            logger.warning("html_extraction_failed", url=url, exc_info=True)
            # Fallback: basic BS4 text extraction
            return self._fallback_extract(html, url)

    def _fallback_extract(self, html: str, url: str) -> ExtractionResult:
        """Simple fallback extraction when readability fails."""
        try:
            soup = BeautifulSoup(html, "lxml")

            # Remove script and style elements
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            text = soup.get_text(separator="\n", strip=True)
            title = ""
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)

            return ExtractionResult(text=text, title=title, metadata={})
        except Exception:
            logger.error("html_fallback_extraction_failed", url=url, exc_info=True)
            return ExtractionResult(text="", metadata={})

    def _extract_metadata(
        self, soup: BeautifulSoup, title: str
    ) -> dict[str, Any]:
        """Extract metadata from HTML meta tags and structured data."""
        metadata: dict[str, Any] = {}

        if title:
            metadata["title"] = title

        # Open Graph and standard meta tags
        meta_mappings = {
            "author": [
                {"name": "author"},
                {"property": "article:author"},
            ],
            "description": [
                {"name": "description"},
                {"property": "og:description"},
            ],
            "published_date": [
                {"name": "date"},
                {"property": "article:published_time"},
                {"name": "DC.date.issued"},
                {"name": "pubdate"},
            ],
            "language": [
                {"http-equiv": "content-language"},
                {"name": "language"},
            ],
        }

        for field, selectors in meta_mappings.items():
            for attrs in selectors:
                tag = soup.find("meta", attrs=attrs)
                if tag and tag.get("content"):
                    metadata[field] = tag["content"]
                    break

        # Check <html lang="...">
        if "language" not in metadata:
            html_tag = soup.find("html")
            if html_tag and html_tag.get("lang"):
                metadata["language"] = html_tag["lang"]

        # Check <time> element for date
        if "published_date" not in metadata:
            time_tag = soup.find("time", attrs={"datetime": True})
            if time_tag:
                metadata["published_date"] = time_tag["datetime"]

        return metadata


class ExtractionResult:
    """Result of content extraction."""

    def __init__(
        self,
        text: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.text = text
        self.title = title
        self.metadata = metadata or {}
