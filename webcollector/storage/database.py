"""SQLite storage layer using SQLAlchemy Core (async with aiosqlite).

Provides the schema definitions and CRUD operations for crawl runs,
documents, attachments, and sources.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = structlog.get_logger(__name__)

metadata = MetaData()

# ── Table definitions ──────────────────────────────────────────────

crawl_runs = Table(
    "crawl_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("prompt", Text, default=""),
    Column("crawl_plan_json", Text, default=""),
    Column("config_snapshot_json", Text, default=""),
    Column("tool_version", String(20), default="0.1.0"),
    Column("status", String(20), default="pending"),
    Column("started_at", DateTime),
    Column("finished_at", DateTime, nullable=True),
    Column("total_urls_discovered", Integer, default=0),
    Column("total_urls_fetched", Integer, default=0),
    Column("total_documents_stored", Integer, default=0),
    Column("total_duplicates_found", Integer, default=0),
    Column("total_errors", Integer, default=0),
    Column("total_bytes_downloaded", Integer, default=0),
    Column("crawlee_storage_dir", Text, nullable=True),
    Column("created_at", DateTime),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("crawl_run_id", String(36), index=True),
    Column("source_url", Text),
    Column("canonical_url", Text, index=True),
    Column("content_hash", String(64), index=True),
    Column("simhash", String(16), nullable=True),
    Column("content_type", String(100)),
    Column("title", Text, nullable=True),
    Column("author", Text, nullable=True),
    Column("published_date", String(20), nullable=True),
    Column("language", String(10), nullable=True),
    Column("extracted_text", Text, nullable=True),
    Column("text_length", Integer, default=0),
    Column("raw_file_path", Text, default=""),
    Column("extracted_file_path", Text, nullable=True),
    Column("file_size_bytes", Integer, default=0),
    Column("fetch_status", Integer, default=0),
    Column("fetch_timestamp", DateTime),
    Column("depth", Integer, default=0),
    Column("parent_url", Text, nullable=True),
    Column("metadata_json", Text, nullable=True),
    Column("is_duplicate", Boolean, default=False),
    Column("duplicate_of_id", String(36), nullable=True),
    Column("created_at", DateTime),
)

attachments = Table(
    "attachments",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", String(36), index=True),
    Column("url", Text),
    Column("filename", String(255)),
    Column("content_type", String(100)),
    Column("content_hash", String(64), index=True),
    Column("file_size_bytes", Integer, default=0),
    Column("raw_file_path", Text),
    Column("extracted_text", Text, nullable=True),
    Column("fetch_status", Integer, default=0),
    Column("created_at", DateTime),
)

sources = Table(
    "sources",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("crawl_run_id", String(36), index=True),
    Column("domain", String(255)),
    Column("seed_url", Text),
    Column("discovery_method", String(20)),
    Column("robots_txt_fetched", Boolean, default=False),
    Column("robots_txt_allows", Boolean, default=True),
    Column("rate_limit_rps", Float, default=1.0),
    Column("pages_crawled", Integer, default=0),
    Column("created_at", DateTime),
)


# ── Database class ─────────────────────────────────────────────────

class Database:
    """Async SQLite database for webcollector storage."""

    def __init__(self, db_path: str) -> None:
        # aiosqlite requires sqlite+aiosqlite:// scheme
        if db_path.startswith("sqlite"):
            self._url = db_path.replace("sqlite://", "sqlite+aiosqlite://", 1)
        else:
            self._url = f"sqlite+aiosqlite:///{db_path}"
        self._engine: AsyncEngine | None = None

    async def init(self) -> None:
        """Create the engine and ensure all tables exist."""
        # Ensure parent directory exists for SQLite file
        if ":///" in self._url:
            from pathlib import Path

            db_file = self._url.split(":///", 1)[1]
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(self._url, echo=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        logger.info("database_initialized", url=self._url)

    async def close(self) -> None:
        """Dispose of the engine."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database not initialized — call init() first")
        return self._engine

    # ── Crawl Runs ─────────────────────────────────────────────

    async def insert_crawl_run(self, run: dict[str, Any]) -> str:
        """Insert a new crawl run. Returns the run ID."""
        async with self.engine.begin() as conn:
            await conn.execute(crawl_runs.insert().values(**run))
        return run["id"]

    async def update_crawl_run(self, run_id: str, updates: dict[str, Any]) -> None:
        """Update fields on an existing crawl run."""
        async with self.engine.begin() as conn:
            await conn.execute(
                crawl_runs.update().where(crawl_runs.c.id == run_id).values(**updates)
            )

    async def get_crawl_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch a crawl run by ID."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(crawl_runs).where(crawl_runs.c.id == run_id)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def list_crawl_runs(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List crawl runs, most recent first."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(crawl_runs)
                .order_by(crawl_runs.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [dict(row) for row in result.mappings().all()]

    async def get_run_plan(self, run_id: str) -> Any:
        """Retrieve the CrawlPlan stored for a run, or None if not found."""
        run = await self.get_crawl_run(run_id)
        if not run or not run.get("crawl_plan_json"):
            return None
        from webcollector.models.crawl_plan import CrawlPlan

        return CrawlPlan.model_validate_json(run["crawl_plan_json"])

    # ── Documents ──────────────────────────────────────────────

    async def insert_document(self, doc: dict[str, Any]) -> str:
        """Insert a document. Returns the document ID."""
        async with self.engine.begin() as conn:
            await conn.execute(documents.insert().values(**doc))
        return doc["id"]

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """Fetch a document by ID."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(documents).where(documents.c.id == doc_id)
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def find_by_content_hash(
        self, content_hash: str, crawl_run_id: str | None = None
    ) -> dict[str, Any] | None:
        """Find a document by content hash, optionally within a specific run."""
        stmt = select(documents).where(documents.c.content_hash == content_hash)
        if crawl_run_id:
            stmt = stmt.where(documents.c.crawl_run_id == crawl_run_id)
        stmt = stmt.limit(1)

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
            return dict(row) if row else None

    async def find_by_canonical_url(
        self, canonical_url: str, crawl_run_id: str | None = None
    ) -> dict[str, Any] | None:
        """Find a document by canonical URL."""
        stmt = select(documents).where(documents.c.canonical_url == canonical_url)
        if crawl_run_id:
            stmt = stmt.where(documents.c.crawl_run_id == crawl_run_id)
        stmt = stmt.limit(1)

        async with self.engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
            return dict(row) if row else None

    async def list_documents(
        self, crawl_run_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List documents for a crawl run."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(documents)
                .where(documents.c.crawl_run_id == crawl_run_id)
                .order_by(documents.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [dict(row) for row in result.mappings().all()]

    async def count_documents(self, crawl_run_id: str) -> int:
        """Count documents in a crawl run."""
        from sqlalchemy import func

        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(func.count())
                .select_from(documents)
                .where(documents.c.crawl_run_id == crawl_run_id)
            )
            return result.scalar() or 0

    async def update_document(self, doc_id: str, updates: dict[str, Any]) -> None:
        """Update fields on a document."""
        async with self.engine.begin() as conn:
            await conn.execute(
                documents.update().where(documents.c.id == doc_id).values(**updates)
            )

    # ── Attachments ────────────────────────────────────────────

    async def insert_attachment(self, attachment: dict[str, Any]) -> str:
        """Insert an attachment. Returns the attachment ID."""
        async with self.engine.begin() as conn:
            await conn.execute(attachments.insert().values(**attachment))
        return attachment["id"]

    async def count_attachments(self, crawl_run_id: str) -> int:
        """Count attachments across all documents in a crawl run."""
        from sqlalchemy import func

        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(func.count())
                .select_from(attachments)
                .where(
                    attachments.c.document_id.in_(
                        select(documents.c.id).where(
                            documents.c.crawl_run_id == crawl_run_id
                        )
                    )
                )
            )
            return result.scalar() or 0

    async def list_attachments(
        self, document_id: str
    ) -> list[dict[str, Any]]:
        """List attachments for a document."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(attachments).where(
                    attachments.c.document_id == document_id
                )
            )
            return [dict(row) for row in result.mappings().all()]

    # ── Sources ────────────────────────────────────────────────

    async def insert_source(self, source: dict[str, Any]) -> str:
        """Insert a source. Returns the source ID."""
        async with self.engine.begin() as conn:
            await conn.execute(sources.insert().values(**source))
        return source["id"]

    async def list_sources(self, crawl_run_id: str) -> list[dict[str, Any]]:
        """List sources for a crawl run."""
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(sources).where(sources.c.crawl_run_id == crawl_run_id)
            )
            return [dict(row) for row in result.mappings().all()]

    async def update_source(self, source_id: str, updates: dict[str, Any]) -> None:
        """Update fields on a source."""
        async with self.engine.begin() as conn:
            await conn.execute(
                sources.update().where(sources.c.id == source_id).values(**updates)
            )
