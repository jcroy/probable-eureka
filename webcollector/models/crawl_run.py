"""CrawlRun and Source models."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from webcollector.models.enums import CrawlRunStatus, DiscoveryMethod


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class CrawlRun(BaseModel):
    id: str = Field(default_factory=_uuid)
    prompt: str = ""
    crawl_plan_json: str = ""
    config_snapshot_json: str = ""
    tool_version: str = "0.1.0"
    status: CrawlRunStatus = CrawlRunStatus.PENDING
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    total_urls_discovered: int = 0
    total_urls_fetched: int = 0
    total_documents_stored: int = 0
    total_duplicates_found: int = 0
    total_errors: int = 0
    total_bytes_downloaded: int = 0
    crawlee_storage_dir: str | None = None
    created_at: datetime = Field(default_factory=_now)


class Source(BaseModel):
    id: str = Field(default_factory=_uuid)
    crawl_run_id: str = ""
    domain: str = ""
    seed_url: str = ""
    discovery_method: DiscoveryMethod = DiscoveryMethod.MANUAL
    robots_txt_fetched: bool = False
    robots_txt_allows: bool = True
    rate_limit_rps: float = 1.0
    pages_crawled: int = 0
    created_at: datetime = Field(default_factory=_now)
