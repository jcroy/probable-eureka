"""Profile matching — fingerprint a page and find the best matching SiteProfile.

The matcher extracts signals from a page's HTML (meta tags, scripts, CSS classes,
URL patterns, DOM elements) and scores each loaded profile against those signals.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from bs4 import BeautifulSoup

from webcollector.profiles.models import SiteProfile
from webcollector.profiles.store import ProfileStore

logger = structlog.get_logger(__name__)


class PageSignals:
    """Extracted fingerprinting signals from a single page."""

    def __init__(
        self,
        url: str = "",
        meta_generator: str = "",
        script_srcs: list[str] | None = None,
        body_classes: str = "",
        html_snippet: str = "",
    ) -> None:
        self.url = url
        self.meta_generator = meta_generator.lower()
        self.script_srcs = [s.lower() for s in (script_srcs or [])]
        self.body_classes = body_classes.lower()
        self.html_snippet = html_snippet

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "meta_generator": self.meta_generator,
            "script_srcs": self.script_srcs,
            "body_classes": self.body_classes,
            "html_snippet_len": len(self.html_snippet),
        }


def extract_signals(url: str, html: str) -> PageSignals:
    """Extract fingerprinting signals from HTML content."""
    soup = BeautifulSoup(html[:50_000], "lxml")

    # Meta generator
    gen_tag = soup.find("meta", attrs={"name": "generator"})
    meta_generator = gen_tag.get("content", "") if gen_tag else ""

    # Script sources
    script_srcs = []
    for script in soup.find_all("script", src=True):
        script_srcs.append(script["src"])

    # Body classes
    body = soup.find("body")
    body_classes = " ".join(body.get("class", [])) if body else ""
    html_tag = soup.find("html")
    if html_tag:
        body_classes += " " + " ".join(html_tag.get("class", []))

    # A snippet of the inner HTML for CSS selector matching
    html_snippet = html[:50_000]

    return PageSignals(
        url=url,
        meta_generator=meta_generator,
        script_srcs=script_srcs,
        body_classes=body_classes,
        html_snippet=html_snippet,
    )


class ProfileMatcher:
    """Scores pages against loaded profiles and returns the best match."""

    def __init__(self, store: ProfileStore) -> None:
        self._store = store

    def match(self, signals: PageSignals) -> SiteProfile | None:
        """Find the best matching profile for the given page signals.

        Returns the profile with the highest score that meets its min_confidence,
        or None if no profile matches.
        """
        best: SiteProfile | None = None
        best_score = 0

        for profile in self._store.all():
            score = self._score(profile, signals)
            fp = profile.fingerprint
            if score >= fp.min_confidence and score > best_score:
                best = profile
                best_score = score

        if best:
            logger.info(
                "profile_matched",
                profile=best.name,
                score=best_score,
                url=signals.url,
            )
        return best

    def _score(self, profile: SiteProfile, signals: PageSignals) -> int:
        """Count how many fingerprint signals match."""
        fp = profile.fingerprint
        score = 0

        # Meta generator matches
        if signals.meta_generator:
            for gen in fp.meta_generators:
                if gen.lower() in signals.meta_generator:
                    score += 1

        # Script src matches
        for pattern in fp.script_patterns:
            pattern_lower = pattern.lower()
            for src in signals.script_srcs:
                if pattern_lower in src:
                    score += 1
                    break  # count each pattern once

        # Body class matches
        if signals.body_classes:
            for pattern in fp.body_class_patterns:
                if pattern.lower() in signals.body_classes:
                    score += 1

        # URL pattern matches
        for pattern in fp.url_patterns:
            try:
                if re.search(pattern, signals.url, re.IGNORECASE):
                    score += 1
            except re.error:
                pass

        # CSS selector matches (search raw HTML for selector indicators)
        soup = BeautifulSoup(signals.html_snippet, "lxml")
        for selector in fp.css_selectors:
            try:
                if soup.select_one(selector):
                    score += 1
            except Exception:
                pass

        return score
