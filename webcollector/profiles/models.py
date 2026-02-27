"""SiteProfile model — describes how a type of site works.

A profile captures the fingerprints, navigation strategy, rendering hints,
and content extraction patterns for a category of websites.  It is NOT
site-specific (e.g. "clerkshq.com") but type-specific (e.g. "clerkbase" —
a platform used by many municipalities).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Fingerprint(BaseModel):
    """Signals that identify a site as matching this profile."""

    # HTML meta generator tag values (case-insensitive substring match)
    # e.g. ["WordPress", "Drupal", "Clerkbase"]
    meta_generators: list[str] = Field(default_factory=list)

    # Script src patterns (substring match against <script src="...">)
    # e.g. ["wp-content/", "jquery.jstree", "angular.min.js"]
    script_patterns: list[str] = Field(default_factory=list)

    # CSS class patterns on <body> or <html> (substring match)
    # e.g. ["wordpress", "clerkbase", "drupal"]
    body_class_patterns: list[str] = Field(default_factory=list)

    # URL path patterns (regex) that indicate this site type
    # e.g. ["/Content/.*\\.htm$", "/wp-json/"]
    url_patterns: list[str] = Field(default_factory=list)

    # HTML elements or attributes that signal this site type
    # e.g. ["div.jstree", "nav#clerkbase-nav", "[data-clerk-id]"]
    css_selectors: list[str] = Field(default_factory=list)

    # Minimum number of fingerprint matches required to consider this a match
    # (avoids false positives from a single generic match)
    min_confidence: int = 2


class NavigationStrategy(BaseModel):
    """How to navigate and discover content on this site type."""

    # Rendering mode hint: "http_only" | "playwright_only" | "adaptive"
    rendering_mode: str = "adaptive"

    # JS interactions to run on pages of this site type
    # These get merged with the CrawlPlan's js_interactions
    js_interactions: list[JsInteractionHint] = Field(default_factory=list)

    # CSS selectors for content listing pages (where links to articles live)
    listing_selectors: list[str] = Field(default_factory=list)

    # CSS selectors for "next page" / pagination
    pagination_selectors: list[str] = Field(default_factory=list)

    # Whether this site type typically needs link discovery from JS-rendered DOM
    needs_js_for_links: bool = False

    # URL pattern for date-based archives (if the site organizes by date)
    # e.g. "/archive/{year}/{month}/" or "/{mon}{dd}_{yy}.htm"
    date_url_pattern: str | None = None


class JsInteractionHint(BaseModel):
    """A JS interaction step to perform (simplified from CrawlPlan's JsInteraction)."""

    action: str  # click, click_all, wait_for_selector, scroll_to_bottom
    selector: str | None = None
    timeout_ms: int = 5000


class ContentPattern(BaseModel):
    """How to find and extract content on this site type."""

    # CSS selector for the main content area
    content_selector: str | None = None

    # CSS selector for the article title
    title_selector: str | None = None

    # CSS selector for published date
    date_selector: str | None = None

    # Regex pattern to extract date from URL (site-type-specific)
    date_url_regex: str | None = None

    # Selectors to remove before extraction (nav, sidebar, footer, etc.)
    remove_selectors: list[str] = Field(default_factory=list)

    # Whether attachment links (PDFs, etc.) are typically in the content area
    has_attachments: bool = False

    # CSS selector for attachment links (more specific than generic <a href=".pdf">)
    attachment_selector: str | None = None


class SiteProfile(BaseModel):
    """Complete profile describing how a type of site works.

    Profiles are independent of crawl plans.  A Clerkbase profile works
    for any municipality using the Clerkbase platform.  A WordPress profile
    works for any WordPress site with standard themes.
    """

    # Unique profile identifier (kebab-case, e.g. "clerkbase", "wordpress-standard")
    name: str

    # Human-readable description
    description: str = ""

    # Version for tracking profile evolution
    version: int = 1

    # When this profile was created/last updated
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # How many times this profile has been successfully used
    usage_count: int = 0

    # Fingerprints to identify this site type
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)

    # How to navigate this site type
    navigation: NavigationStrategy = Field(default_factory=NavigationStrategy)

    # How to extract content from this site type
    content: ContentPattern = Field(default_factory=ContentPattern)

    # Free-form notes (from LLM or human) about this site type
    notes: str = ""
