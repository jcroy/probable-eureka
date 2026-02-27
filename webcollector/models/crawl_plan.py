"""CrawlPlan: the structured output from query interpretation."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


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
