"""Rule-based metadata extraction from text and HTML.

Extracts structured metadata (title, author, date, language) from both
HTML meta tags (via the HTML extractor) and text-level heuristics.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Common date patterns to try parsing
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%B %d, %Y",
    "%b %d, %Y",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
]

# Regex for ISO-like date in text
_DATE_RE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"
    r"|"
    r"\b(\w+ \d{1,2},? \d{4})\b"
)


def extract_metadata(
    html_metadata: dict[str, Any],
    text: str = "",
) -> DocumentMetadata:
    """Build a DocumentMetadata from HTML-level metadata and text heuristics.

    Priority: HTML meta tags > text-level heuristics.
    """
    title = html_metadata.get("title", "")
    author = html_metadata.get("author")
    language = html_metadata.get("language")
    published_date = _parse_date(html_metadata.get("published_date"))

    # If no date from HTML, try to find one in the text
    if not published_date and text:
        published_date = _find_date_in_text(text[:2000])

    # Detect language if not already set
    if not language and text:
        language = _detect_language(text[:5000])

    return DocumentMetadata(
        title=title,
        author=author,
        published_date=published_date,
        language=language,
    )


def _parse_date(raw: str | None) -> date | None:
    """Try to parse a date string using common formats."""
    if not raw:
        return None

    raw = raw.strip()

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.date()
        except ValueError:
            continue

    return None


def _find_date_in_text(text: str) -> date | None:
    """Search for a date pattern near the top of the text."""
    match = _DATE_RE.search(text)
    if not match:
        return None

    raw = match.group(1) or match.group(2)
    return _parse_date(raw)


def _detect_language(text: str) -> str | None:
    """Detect language using langdetect. Returns ISO 639-1 code or None."""
    try:
        from langdetect import detect

        lang = detect(text)
        return lang if lang else None
    except Exception:
        return None


class DocumentMetadata:
    """Structured metadata extracted from a document."""

    def __init__(
        self,
        title: str = "",
        author: str | None = None,
        published_date: date | None = None,
        language: str | None = None,
    ) -> None:
        self.title = title
        self.author = author
        self.published_date = published_date
        self.language = language

    def to_dict(self) -> dict[str, Any]:
        """Convert to a serializable dictionary."""
        return {
            "title": self.title,
            "author": self.author,
            "published_date": (
                self.published_date.isoformat() if self.published_date else None
            ),
            "language": self.language,
        }
