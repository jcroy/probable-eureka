"""Enums and constants for webcollector."""

from enum import StrEnum


class CrawlRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DiscoveryMethod(StrEnum):
    SITEMAP = "sitemap"
    SEARCH = "search"
    LINK_GRAPH = "link_graph"
    MANUAL = "manual"
    RSS = "rss"


class FetchStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
