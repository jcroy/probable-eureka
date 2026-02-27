"""Tests for the crawl handler scope logic and the orchestrator page processing pipeline.

These tests exercise the critical glue code: CrawlHandlers scope filtering,
rate limiter timing, and the RunOrchestrator's on_page_crawled callback that
ties extraction → dedup → database storage together.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from webcollector.config import WebCollectorConfig
from webcollector.crawl.handlers import CrawlHandlers
from webcollector.crawl.rate_limiter import DomainRateLimiter
from webcollector.models.crawl_plan import CrawlPlan
from webcollector.orchestrator import RunOrchestrator
from webcollector.storage.database import Database


# ── Fixtures ───────────────────────────────────────────────────────


def _make_plan(**overrides) -> CrawlPlan:
    defaults = {
        "seed_urls": ["http://example.com"],
        "target_domains": ["example.com"],
        "max_depth": 2,
        "max_pages": 10,
    }
    defaults.update(overrides)
    return CrawlPlan(**defaults)


def _make_handlers(plan: CrawlPlan | None = None) -> CrawlHandlers:
    plan = plan or _make_plan()
    return CrawlHandlers(
        plan=plan,
        rate_limiter=DomainRateLimiter(default_rps=100),  # fast for tests
        downloader=AsyncMock(),
    )


def _make_html(title: str, body: str) -> str:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><article><p>{body}</p></article></body></html>"
    )


# ── CrawlHandlers scope tests ─────────────────────────────────────


class TestHandlerScope:
    def test_in_scope_same_domain(self):
        h = _make_handlers()
        assert h._is_in_scope("http://example.com/page") is True

    def test_out_of_scope_different_domain(self):
        h = _make_handlers()
        assert h._is_in_scope("http://other.com/page") is False

    def test_subdomain_in_scope(self):
        h = _make_handlers()
        assert h._is_in_scope("http://www.example.com/page") is True

    def test_no_domain_restriction(self):
        h = _make_handlers(_make_plan(target_domains=[]))
        assert h._is_in_scope("http://anywhere.com/page") is True

    def test_exclude_pattern(self):
        plan = _make_plan(exclude_patterns=[r"/admin/", r"\?debug="])
        h = _make_handlers(plan)
        assert h._is_in_scope("http://example.com/admin/settings") is False
        assert h._is_in_scope("http://example.com/page?debug=1") is False
        assert h._is_in_scope("http://example.com/page") is True

    def test_include_pattern(self):
        plan = _make_plan(url_patterns=[r"/docs/", r"/api/"])
        h = _make_handlers(plan)
        assert h._is_in_scope("http://example.com/docs/intro") is True
        assert h._is_in_scope("http://example.com/api/v1") is True
        assert h._is_in_scope("http://example.com/blog/post") is False

    def test_include_and_exclude_together(self):
        plan = _make_plan(
            url_patterns=[r"/docs/"],
            exclude_patterns=[r"/docs/internal"],
        )
        h = _make_handlers(plan)
        assert h._is_in_scope("http://example.com/docs/public") is True
        assert h._is_in_scope("http://example.com/docs/internal/secret") is False


class TestHandlerDocType:
    def test_wanted_pdf(self):
        plan = _make_plan(document_types=["pdf", "docx"])
        h = _make_handlers(plan)
        assert h._is_wanted_doc_type("http://example.com/file.pdf") is True

    def test_wanted_docx(self):
        plan = _make_plan(document_types=["pdf", "docx"])
        h = _make_handlers(plan)
        assert h._is_wanted_doc_type("http://example.com/file.docx") is True

    def test_no_extension_allowed(self):
        """URLs without a matching extension are allowed (can't determine type)."""
        plan = _make_plan(document_types=["pdf"])
        h = _make_handlers(plan)
        assert h._is_wanted_doc_type("http://example.com/download?id=123") is True

    def test_no_type_restriction(self):
        plan = _make_plan(document_types=[])
        h = _make_handlers(plan)
        assert h._is_wanted_doc_type("http://example.com/anything.xyz") is True


# ── Rate limiter tests ─────────────────────────────────────────────


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_does_not_raise(self):
        rl = DomainRateLimiter(default_rps=100)
        await rl.acquire("example.com")  # should not raise

    @pytest.mark.asyncio
    async def test_per_domain_override(self):
        rl = DomainRateLimiter(default_rps=1.0)
        rl.set_domain_rps("fast.com", 100.0)
        assert rl._get_delay("fast.com") == pytest.approx(0.01, abs=0.001)
        assert rl._get_delay("default.com") == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_zero_rps_no_delay(self):
        rl = DomainRateLimiter(default_rps=0)
        assert rl._get_delay("any.com") == 0.0


# ── Orchestrator pipeline tests ───────────────────────────────────


@pytest_asyncio.fixture
async def orchestrator_with_db():
    """Create an orchestrator wired to a real temp database (no crawler)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = WebCollectorConfig()
        config.storage.db_path = os.path.join(tmpdir, "test.db")
        config.storage.file_store_path = tmpdir

        plan = _make_plan()
        orch = RunOrchestrator(config=config, plan=plan, prompt="test")

        # Init DB manually so we can test callbacks without running the crawler
        orch._db = Database(config.storage.db_path)
        await orch._db.init()

        yield orch

        await orch._db.close()


class TestOrchestratorPageProcessing:
    @pytest.mark.asyncio
    async def test_processes_html_page(self, orchestrator_with_db):
        """A valid HTML page should be extracted and stored as a document."""
        orch = orchestrator_with_db

        page_data = {
            "url": "http://example.com/test-page",
            "html": _make_html("Test Title", "This is meaningful article content for extraction."),
            "status_code": 200,
            "content_type": "text/html",
        }

        await orch._on_page_crawled(page_data)

        assert orch._stats.documents_stored == 1
        assert orch._stats.errors == 0

        # Verify it's actually in the database
        docs = await orch._db.list_documents(orch.run_id, limit=10)
        assert len(docs) == 1
        assert docs[0]["source_url"] == "http://example.com/test-page"
        assert docs[0]["title"] == "Test Title"
        assert docs[0]["text_length"] > 0

    @pytest.mark.asyncio
    async def test_skips_empty_content(self, orchestrator_with_db):
        """Pages with no extractable text should be skipped."""
        orch = orchestrator_with_db

        page_data = {
            "url": "http://example.com/empty",
            "html": "<html><body></body></html>",
            "status_code": 200,
            "content_type": "text/html",
        }

        await orch._on_page_crawled(page_data)

        assert orch._stats.documents_stored == 0

    @pytest.mark.asyncio
    async def test_dedup_exact_duplicate(self, orchestrator_with_db):
        """Sending the same page twice should count as a duplicate."""
        orch = orchestrator_with_db
        html = _make_html("Dup Page", "This content is exactly duplicated across pages.")

        await orch._on_page_crawled({
            "url": "http://example.com/page-1",
            "html": html,
            "status_code": 200,
            "content_type": "text/html",
        })
        await orch._on_page_crawled({
            "url": "http://example.com/page-2",
            "html": html,
            "status_code": 200,
            "content_type": "text/html",
        })

        assert orch._stats.documents_stored == 1
        assert orch._stats.duplicates_found == 1

    @pytest.mark.asyncio
    async def test_different_pages_both_stored(self, orchestrator_with_db):
        """Two distinct pages should both be stored."""
        orch = orchestrator_with_db

        await orch._on_page_crawled({
            "url": "http://example.com/alpha",
            "html": _make_html("Alpha", "First unique article about alpha topics."),
            "status_code": 200,
            "content_type": "text/html",
        })
        await orch._on_page_crawled({
            "url": "http://example.com/beta",
            "html": _make_html("Beta", "Second unique article about beta topics."),
            "status_code": 200,
            "content_type": "text/html",
        })

        assert orch._stats.documents_stored == 2
        assert orch._stats.duplicates_found == 0

    @pytest.mark.asyncio
    async def test_metadata_extracted(self, orchestrator_with_db):
        """Metadata from HTML meta tags should be stored."""
        orch = orchestrator_with_db

        html = (
            '<html lang="en"><head>'
            '<title>Metadata Test</title>'
            '<meta name="author" content="Jane Doe">'
            "</head><body><article>"
            "<p>Article body with enough content to extract.</p>"
            "</article></body></html>"
        )

        await orch._on_page_crawled({
            "url": "http://example.com/meta",
            "html": html,
            "status_code": 200,
            "content_type": "text/html",
        })

        docs = await orch._db.list_documents(orch.run_id)
        assert len(docs) == 1
        assert docs[0]["title"] == "Metadata Test"
        assert docs[0]["language"] == "en"

    @pytest.mark.asyncio
    async def test_raw_html_file_written(self, orchestrator_with_db):
        """The raw HTML should be written to the filesystem."""
        orch = orchestrator_with_db

        await orch._on_page_crawled({
            "url": "http://example.com/stored",
            "html": _make_html("Stored", "Content that gets written to disk."),
            "status_code": 200,
            "content_type": "text/html",
        })

        docs = await orch._db.list_documents(orch.run_id)
        raw_path = docs[0]["raw_file_path"]
        assert raw_path.endswith(".html")

        # Verify file exists on disk
        full_path = os.path.join(
            orch._config.storage.file_store_path, "raw", raw_path
        )
        assert os.path.exists(full_path)
