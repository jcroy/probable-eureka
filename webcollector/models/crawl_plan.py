"""CrawlPlan: the structured output from query interpretation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class JsInteractionStep(BaseModel):
    """A single JS interaction to perform on a page (click, wait, scroll, etc.)."""

    action: str  # click, click_all, wait_for_selector, wait_for_timeout, scroll_to_bottom
    selector: str | None = None
    timeout_ms: int = 5000


class JsInteraction(BaseModel):
    """JS interactions to run on pages matching a URL pattern."""

    url_pattern: str  # regex — pages matching this get the interactions
    steps: list[JsInteractionStep]


class CrawlPlan(BaseModel):
    """Structured crawl plan generated from a user prompt (by LLM or manual YAML)."""

    intent_summary: str = ""
    target_domains: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    url_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    date_range_start: date | None = None
    date_range_end: date | None = None
    document_types: list[str] = Field(default_factory=lambda: ["html", "pdf"])
    max_depth: int = 3
    max_pages: int = 1000
    keywords: list[str] = Field(default_factory=list)
    adapter_hint: str | None = None
    js_interactions: list[JsInteraction] = Field(default_factory=list)
