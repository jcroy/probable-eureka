"""Tests for webcollector.storage.database."""

import os
import tempfile
from datetime import datetime

import pytest
import pytest_asyncio

from webcollector.storage.database import Database


@pytest_asyncio.fixture
async def db():
    """Create a fresh temp database for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        database = Database(db_path)
        await database.init()
        yield database
        await database.close()


def _make_run(run_id: str = "run-1", **overrides) -> dict:
    defaults = {
        "id": run_id,
        "prompt": "test prompt",
        "crawl_plan_json": "{}",
        "config_snapshot_json": "{}",
        "tool_version": "0.1.0",
        "status": "running",
        "started_at": datetime.utcnow(),
        "total_urls_discovered": 0,
        "total_urls_fetched": 0,
        "total_documents_stored": 0,
        "total_duplicates_found": 0,
        "total_errors": 0,
        "total_bytes_downloaded": 0,
        "created_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    return defaults


def _make_doc(doc_id: str = "doc-1", run_id: str = "run-1", **overrides) -> dict:
    defaults = {
        "id": doc_id,
        "crawl_run_id": run_id,
        "source_url": "http://example.com/page",
        "canonical_url": "http://example.com/page",
        "content_hash": "abc123",
        "content_type": "text/html",
        "extracted_text": "Hello world",
        "text_length": 11,
        "raw_file_path": "",
        "file_size_bytes": 100,
        "fetch_status": 200,
        "fetch_timestamp": datetime.utcnow(),
        "depth": 0,
        "is_duplicate": False,
        "created_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    return defaults


class TestCrawlRuns:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, db: Database):
        run = _make_run()
        await db.insert_crawl_run(run)
        fetched = await db.get_crawl_run("run-1")
        assert fetched is not None
        assert fetched["prompt"] == "test prompt"
        assert fetched["status"] == "running"

    @pytest.mark.asyncio
    async def test_update(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.update_crawl_run("run-1", {"status": "completed"})
        fetched = await db.get_crawl_run("run-1")
        assert fetched["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_runs(self, db: Database):
        await db.insert_crawl_run(_make_run("run-1"))
        await db.insert_crawl_run(_make_run("run-2"))
        runs = await db.list_crawl_runs()
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db: Database):
        result = await db.get_crawl_run("nonexistent")
        assert result is None


class TestDocuments:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, db: Database):
        await db.insert_crawl_run(_make_run())
        doc = _make_doc()
        await db.insert_document(doc)
        fetched = await db.get_document("doc-1")
        assert fetched is not None
        assert fetched["source_url"] == "http://example.com/page"

    @pytest.mark.asyncio
    async def test_find_by_content_hash(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.insert_document(_make_doc(content_hash="unique_hash"))
        found = await db.find_by_content_hash("unique_hash")
        assert found is not None
        assert found["id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_find_by_canonical_url(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.insert_document(_make_doc())
        found = await db.find_by_canonical_url("http://example.com/page")
        assert found is not None

    @pytest.mark.asyncio
    async def test_count(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.insert_document(_make_doc("doc-1"))
        await db.insert_document(_make_doc("doc-2", content_hash="def456"))
        count = await db.count_documents("run-1")
        assert count == 2

    @pytest.mark.asyncio
    async def test_list_documents(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.insert_document(_make_doc("doc-1"))
        await db.insert_document(_make_doc("doc-2", content_hash="other"))
        docs = await db.list_documents("run-1")
        assert len(docs) == 2


class TestAttachments:
    @pytest.mark.asyncio
    async def test_insert_and_list(self, db: Database):
        await db.insert_crawl_run(_make_run())
        await db.insert_document(_make_doc())
        attachment = {
            "id": "att-1",
            "document_id": "doc-1",
            "url": "http://example.com/file.pdf",
            "filename": "file.pdf",
            "content_type": "application/pdf",
            "content_hash": "pdfhash",
            "file_size_bytes": 5000,
            "raw_file_path": "ab/abc.pdf",
            "fetch_status": 200,
            "created_at": datetime.utcnow(),
        }
        await db.insert_attachment(attachment)
        atts = await db.list_attachments("doc-1")
        assert len(atts) == 1
        assert atts[0]["filename"] == "file.pdf"
