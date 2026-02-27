"""Tests for the post-crawl validation report."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from webcollector.models.crawl_plan import CrawlPlan
from webcollector.reporting.validation import validate_run
from webcollector.storage.database import Database


def _make_plan(**overrides) -> CrawlPlan:
    defaults = {
        "seed_urls": ["http://example.com"],
        "target_domains": ["example.com"],
        "max_depth": 2,
        "max_pages": 10,
    }
    defaults.update(overrides)
    return CrawlPlan(**defaults)


def _make_doc(run_id: str, **overrides) -> dict:
    doc_id = str(uuid4())
    defaults = {
        "id": doc_id,
        "crawl_run_id": run_id,
        "source_url": f"http://example.com/{doc_id[:8]}",
        "canonical_url": f"http://example.com/{doc_id[:8]}",
        "content_hash": doc_id.replace("-", "")[:64].ljust(64, "0"),
        "simhash": None,
        "content_type": "text/html",
        "title": "Test Document",
        "author": None,
        "published_date": None,
        "language": "en",
        "extracted_text": "Some test content that is long enough.",
        "text_length": 300,
        "raw_file_path": "ab/abcdef.html",
        "file_size_bytes": 1000,
        "fetch_status": 200,
        "fetch_timestamp": datetime.utcnow(),
        "depth": 0,
        "parent_url": None,
        "metadata_json": "{}",
        "is_duplicate": False,
        "duplicate_of_id": None,
        "created_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    return defaults


@pytest_asyncio.fixture
async def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(os.path.join(tmpdir, "test.db"))
        await db.init()
        yield db
        await db.close()


class TestValidateRun:
    @pytest.mark.asyncio
    async def test_empty_run(self, db):
        """Validation report for a run with no documents."""
        run_id = str(uuid4())
        plan = _make_plan()
        report = await validate_run(db, run_id, plan)

        assert report.total_documents == 0
        assert report.total_attachments == 0
        assert report.docs_with_date == 0
        assert report.docs_without_date == 0
        assert report.avg_text_length == 0.0

    @pytest.mark.asyncio
    async def test_mixed_docs_stats(self, db):
        """Report correctly computes stats from mixed documents."""
        run_id = str(uuid4())
        plan = _make_plan(
            date_range_start=date(2026, 2, 25),
            date_range_end=date(2026, 2, 28),
        )

        # Insert docs with varying properties
        docs = [
            _make_doc(run_id, title="Doc A", published_date="2026-02-26", text_length=500),
            _make_doc(run_id, title="Doc B", published_date="2026-02-27", text_length=1200),
            _make_doc(run_id, title=None, published_date=None, text_length=50),
            _make_doc(
                run_id,
                title="Doc D",
                published_date="2026-02-26",
                text_length=800,
                is_duplicate=True,
                source_url="http://sub.example.com/page",
                canonical_url="http://sub.example.com/page",
            ),
        ]
        for doc in docs:
            await db.insert_document(doc)

        report = await validate_run(db, run_id, plan)

        assert report.total_documents == 4
        assert report.docs_with_date == 3
        assert report.docs_without_date == 1
        assert report.docs_with_title == 3
        assert report.docs_without_title == 1
        assert report.short_docs == 1  # text_length=50 < 100
        assert report.near_duplicates == 1
        assert report.avg_text_length == (500 + 1200 + 50 + 800) / 4
        assert report.min_text_length == 50
        assert report.max_text_length == 1200

    @pytest.mark.asyncio
    async def test_date_distribution(self, db):
        """Date distribution is computed correctly."""
        run_id = str(uuid4())
        plan = _make_plan(
            date_range_start=date(2026, 2, 25),
            date_range_end=date(2026, 2, 28),
        )

        docs = [
            _make_doc(run_id, published_date="2026-02-26"),
            _make_doc(run_id, published_date="2026-02-26"),
            _make_doc(run_id, published_date="2026-02-27"),
        ]
        for doc in docs:
            await db.insert_document(doc)

        report = await validate_run(db, run_id, plan)

        assert report.date_distribution == {"2026-02-26": 2, "2026-02-27": 1}

    @pytest.mark.asyncio
    async def test_docs_outside_range_detected(self, db):
        """Documents with dates outside the requested range are flagged."""
        run_id = str(uuid4())
        plan = _make_plan(
            date_range_start=date(2026, 2, 25),
            date_range_end=date(2026, 2, 27),
        )

        docs = [
            _make_doc(run_id, published_date="2026-02-26"),  # in range
            _make_doc(run_id, published_date="2026-01-01"),  # before range
            _make_doc(run_id, published_date="2026-03-15"),  # after range
        ]
        for doc in docs:
            await db.insert_document(doc)

        report = await validate_run(db, run_id, plan)

        assert report.docs_outside_range == 2

    @pytest.mark.asyncio
    async def test_domain_distribution(self, db):
        """Domain distribution is computed correctly."""
        run_id = str(uuid4())
        plan = _make_plan()

        docs = [
            _make_doc(
                run_id,
                source_url="http://example.com/a",
                canonical_url="http://example.com/a",
            ),
            _make_doc(
                run_id,
                source_url="http://www.example.com/b",
                canonical_url="http://www.example.com/b",
            ),
            _make_doc(
                run_id,
                source_url="http://other.com/c",
                canonical_url="http://other.com/c",
            ),
        ]
        for doc in docs:
            await db.insert_document(doc)

        report = await validate_run(db, run_id, plan)

        # www.example.com should be normalized to example.com
        assert report.domain_distribution.get("example.com") == 2
        assert report.domain_distribution.get("other.com") == 1

    @pytest.mark.asyncio
    async def test_no_date_range_in_report(self, db):
        """When plan has no date range, date_range_requested is (None, None)."""
        run_id = str(uuid4())
        plan = _make_plan()

        await db.insert_document(_make_doc(run_id, published_date="2026-02-26"))

        report = await validate_run(db, run_id, plan)

        assert report.date_range_requested == (None, None)
        assert report.docs_outside_range == 0

    @pytest.mark.asyncio
    async def test_run_stats_passed_through(self, db):
        """Run-level stats (errors, duplicates) are passed through."""
        run_id = str(uuid4())
        plan = _make_plan()
        run_stats = {
            "total_errors": 5,
            "total_duplicates_found": 3,
            "filtered_by_date": 7,
        }

        report = await validate_run(db, run_id, plan, run_stats=run_stats)

        assert report.total_errors == 5
        assert report.exact_duplicates == 3
        assert report.filtered_by_date == 7
