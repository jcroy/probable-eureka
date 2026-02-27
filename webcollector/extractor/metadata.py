"""Rule-based metadata extraction from text and HTML.

Extracts structured metadata (title, author, date, language) from both
HTML meta tags (via the HTML extractor) and text-level heuristics.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

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

# Prose date pattern: "the 8th day of September 2025" (common in government minutes)
_PROSE_DATE_RE = re.compile(
    r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+(\w+)\s+(\d{4})\b",
    re.IGNORECASE,
)

# 3-letter month abbreviations for URL date extraction
_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# URL date patterns, tried in order (most specific first):
#
# 1. /2025/11/24/slug  or /2025/11/24-slug  (WordPress, most blogs/news)
# 2. /2025-11-24-slug  or /2025-11-24/slug  (Jekyll, Hugo, static site generators)
# 3. /20251124-slug  or /20251124.html      (compact ISO in filename)
# 4. /2025/nov/24/slug                      (Guardian-style month abbreviation)
# 5. nov24_25tc.htm                         (Clerkbase-style: {mon}{dd}_{yy})
_URL_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # /YYYY/MM/DD (path segments)
    (re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$|-|\.)"), "ymd_slash"),
    # /YYYY-MM-DD (hyphenated in path segment or filename)
    (re.compile(r"/(\d{4})-(\d{1,2})-(\d{1,2})(?:/|$|-)"), "ymd_hyphen"),
    # /YYYYMMDD (compact 8-digit in filename or path)
    (re.compile(r"/(\d{4})(\d{2})(\d{2})(?:\D|$)"), "ymd_compact"),
    # /YYYY/mon/DD (month abbreviation in path)
    (re.compile(r"/(\d{4})/([a-z]{3})/(\d{1,2})(?:/|$)", re.IGNORECASE), "y_mon_d"),
    # mon{dd}_{yy} in filename (Clerkbase-style)
    (re.compile(r"([a-z]{3})(\d{1,2})_(\d{2})", re.IGNORECASE), "mon_d_yy"),
]


def extract_metadata(
    html_metadata: dict[str, Any],
    text: str = "",
    url: str = "",
) -> DocumentMetadata:
    """Build a DocumentMetadata from HTML-level metadata and text heuristics.

    Priority: HTML meta tags > URL-based date > prose date in text > regex date in text.
    """
    title = html_metadata.get("title", "")
    author = html_metadata.get("author")
    language = html_metadata.get("language")
    published_date = _parse_date(html_metadata.get("published_date"))

    # If no date from HTML meta tags, try the URL
    if not published_date and url:
        published_date = _find_date_in_url(url)

    # If no date from URL, try prose pattern in text ("the Nth day of Month Year")
    if not published_date and text:
        published_date = _find_prose_date_in_text(text[:2000])

    # If still no date, fall back to generic date regex in text
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


def _find_date_in_url(url: str) -> date | None:
    """Extract a date from URL path components.

    Tries multiple common URL date patterns (most specific first):
    - /2025/11/24/article-slug   (WordPress, most blogs)
    - /2025-11-24-article-slug   (Jekyll, Hugo)
    - /20251124-article.html     (compact ISO)
    - /2025/nov/24/headline      (Guardian-style)
    - /nov24_25tc.htm            (Clerkbase-style)
    """
    try:
        path = urlparse(url).path
    except Exception:
        return None

    for pattern, kind in _URL_DATE_PATTERNS:
        match = pattern.search(path)
        if not match:
            continue
        try:
            if kind in ("ymd_slash", "ymd_hyphen", "ymd_compact"):
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif kind == "y_mon_d":
                year = int(match.group(1))
                month = _MONTH_ABBR.get(match.group(2).lower(), 0)
                day = int(match.group(3))
                if not month:
                    continue
            elif kind == "mon_d_yy":
                month = _MONTH_ABBR.get(match.group(1).lower(), 0)
                if not month:
                    continue
                day = int(match.group(2))
                year = 2000 + int(match.group(3))
            else:
                continue
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _find_prose_date_in_text(text: str) -> date | None:
    """Find prose dates like 'the 8th day of September 2025' in text."""
    match = _PROSE_DATE_RE.search(text)
    if not match:
        return None
    day_str, month_str, year_str = match.group(1), match.group(2), match.group(3)
    return _parse_date(f"{day_str} {month_str} {year_str}")


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
