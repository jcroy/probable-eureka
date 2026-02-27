"""Post-crawl validation report — summarizes data quality for a completed run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlparse

from webcollector.models.crawl_plan import CrawlPlan
from webcollector.storage.database import Database


@dataclass
class ValidationReport:
    """Quality summary of a completed crawl run."""

    total_documents: int = 0
    total_attachments: int = 0

    # Date coverage
    date_range_requested: tuple[date | None, date | None] = (None, None)
    docs_with_date: int = 0
    docs_without_date: int = 0
    date_distribution: dict[str, int] = field(default_factory=dict)
    docs_outside_range: int = 0

    # Content quality
    docs_with_title: int = 0
    docs_without_title: int = 0
    avg_text_length: float = 0.0
    min_text_length: int = 0
    max_text_length: int = 0
    short_docs: int = 0  # text_length < 100 chars

    # Dedup
    exact_duplicates: int = 0
    near_duplicates: int = 0

    # Domains
    domain_distribution: dict[str, int] = field(default_factory=dict)

    # Errors / filtering
    total_errors: int = 0
    filtered_by_date: int = 0


async def validate_run(
    db: Database,
    run_id: str,
    plan: CrawlPlan,
    run_stats: dict[str, Any] | None = None,
) -> ValidationReport:
    """Build a ValidationReport by querying stored documents for the run.

    Args:
        db: Initialized Database instance.
        run_id: The crawl run ID.
        plan: The CrawlPlan used for this run.
        run_stats: Optional dict with run-level counters (from crawl_runs table).
    """
    docs = await db.list_documents(run_id, limit=10000)
    attachment_count = await db.count_attachments(run_id)

    report = ValidationReport(
        total_documents=len(docs),
        total_attachments=attachment_count,
        date_range_requested=(plan.date_range_start, plan.date_range_end),
    )

    # Pull error/filter counts from the run record
    if run_stats:
        report.total_errors = run_stats.get("total_errors", 0)
        report.exact_duplicates = run_stats.get("total_duplicates_found", 0)

    text_lengths: list[int] = []
    date_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()

    for doc in docs:
        # Date coverage
        pub_date_str = doc.get("published_date")
        if pub_date_str:
            report.docs_with_date += 1
            date_counter[pub_date_str] += 1
            # Check if outside requested range
            try:
                pub = date.fromisoformat(pub_date_str)
                if plan.date_range_start and pub < plan.date_range_start:
                    report.docs_outside_range += 1
                elif plan.date_range_end and pub > plan.date_range_end:
                    report.docs_outside_range += 1
            except ValueError:
                pass
        else:
            report.docs_without_date += 1

        # Content quality
        title = doc.get("title")
        if title:
            report.docs_with_title += 1
        else:
            report.docs_without_title += 1

        tl = doc.get("text_length", 0) or 0
        text_lengths.append(tl)
        if tl < 100:
            report.short_docs += 1

        # Near-duplicate tracking
        if doc.get("is_duplicate"):
            report.near_duplicates += 1

        # Domain distribution
        url = doc.get("source_url", "")
        if url:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                domain_counter[domain] += 1
            except Exception:
                pass

    # Aggregate text length stats
    if text_lengths:
        report.avg_text_length = sum(text_lengths) / len(text_lengths)
        report.min_text_length = min(text_lengths)
        report.max_text_length = max(text_lengths)

    # Sort date distribution chronologically, domain distribution by count desc
    report.date_distribution = dict(sorted(date_counter.items()))
    report.domain_distribution = dict(
        sorted(domain_counter.items(), key=lambda x: x[1], reverse=True)
    )

    # Pull filtered_by_date from run stats if available
    # (stored on the run record, not on individual docs)
    if run_stats:
        report.filtered_by_date = run_stats.get("filtered_by_date", 0)

    return report
