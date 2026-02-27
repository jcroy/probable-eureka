"""Document and Attachment models."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Document(BaseModel):
    id: str = Field(default_factory=_uuid)
    crawl_run_id: str = ""
    source_url: str = ""
    canonical_url: str = ""
    content_hash: str = ""
    simhash: str = ""
    content_type: str = ""
    title: str | None = None
    author: str | None = None
    published_date: date | None = None
    language: str | None = None
    extracted_text: str | None = None
    text_length: int = 0
    raw_file_path: str = ""
    extracted_file_path: str | None = None
    file_size_bytes: int = 0
    fetch_status: int = 0
    fetch_timestamp: datetime = Field(default_factory=_now)
    depth: int = 0
    parent_url: str | None = None
    metadata_json: str | None = None
    is_duplicate: bool = False
    duplicate_of_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class Attachment(BaseModel):
    id: str = Field(default_factory=_uuid)
    document_id: str = ""
    url: str = ""
    filename: str = ""
    content_type: str = ""
    content_hash: str = ""
    file_size_bytes: int = 0
    raw_file_path: str = ""
    extracted_text: str | None = None
    fetch_status: int = 0
    created_at: datetime = Field(default_factory=_now)
