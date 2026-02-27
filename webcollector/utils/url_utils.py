"""URL parsing, normalization, and scope checking utilities."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

# Query params to strip during normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "mc_cid", "mc_eid",
}

# File extensions that indicate downloadable documents
DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".pptx", ".ppt", ".txt", ".rtf", ".odt", ".xml",
}


def _safe_urlparse(url: str) -> tuple:
    """Parse a URL, returning an empty result for malformed URLs.

    urlparse can raise ValueError on malformed IPv6 addresses and other
    edge cases. This wrapper catches those and returns a blank ParseResult.
    """
    try:
        return urlparse(url)
    except ValueError:
        return urlparse("")


def normalize_url(url: str) -> str:
    """Normalize a URL to a canonical form for dedup.

    - Lowercases scheme and host
    - Strips fragments
    - Sorts query params
    - Removes known tracking params

    Returns the original URL unchanged if parsing fails.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Strip fragment
    fragment = ""

    # Sort and filter query params
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)

    # Normalize path (remove trailing slash for non-root)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, fragment))


def get_domain(url: str) -> str:
    """Extract domain from a URL.

    Returns empty string for malformed URLs.
    """
    return _safe_urlparse(url).netloc.lower()


def is_document_url(url: str) -> bool:
    """Check if a URL points to a downloadable document (by extension)."""
    path = _safe_urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOCUMENT_EXTENSIONS)


def resolve_url(base_url: str, relative_url: str) -> str:
    """Resolve a relative URL against a base URL."""
    return urljoin(base_url, relative_url)


def url_matches_patterns(url: str, patterns: list[str]) -> bool:
    """Check if a URL matches any of the given regex patterns."""
    return any(re.search(pattern, url) for pattern in patterns)


def is_same_domain(url: str, domain: str) -> bool:
    """Check if a URL belongs to the given domain."""
    url_domain = get_domain(url)
    return url_domain == domain or url_domain.endswith("." + domain)
