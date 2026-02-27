"""Run orchestrator — ties crawler, extraction, dedup, and storage together.

This is the main entry point for executing a crawl. It:
1. Creates a crawl run record in the database.
2. Launches the Crawlee crawler with the crawl plan.
3. As pages are crawled, extracts content and metadata.
4. Deduplicates documents.
5. Stores results in the database and filesystem.
6. Returns a summary of what was collected.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from webcollector.crawl.crawler import CrawlRunner
from webcollector.crawl.downloader import DownloadResult
from webcollector.extractor.html_extractor import HTMLExtractor
from webcollector.extractor.metadata import extract_metadata
from webcollector.extractor.pdf_extractor import PDFExtractor
from webcollector.storage.database import Database
from webcollector.storage.dedup import DedupChecker, content_hash_text
from webcollector.utils.file_utils import content_addressed_path, ensure_dir
from webcollector.utils.hashing import sha256_hex
from webcollector.utils.url_utils import normalize_url

if TYPE_CHECKING:
    from webcollector.config import WebCollectorConfig
    from webcollector.models.crawl_plan import CrawlPlan

logger = structlog.get_logger(__name__)


class RunOrchestrator:
    """Orchestrates a complete crawl-extract-store run."""

    def __init__(
        self,
        config: WebCollectorConfig,
        plan: CrawlPlan,
        prompt: str = "",
        enable_profiles: bool = True,
    ) -> None:
        self._config = config
        self._plan = plan
        self._prompt = prompt
        self._enable_profiles = enable_profiles
        self._run_id = str(uuid4())
        self._db: Database | None = None
        self._html_extractor = HTMLExtractor()
        self._pdf_extractor = PDFExtractor(config.extraction)
        self._dedup = DedupChecker()
        self._stats = RunStats()

    @property
    def run_id(self) -> str:
        return self._run_id

    async def execute(self) -> RunResult:
        """Execute the full crawl pipeline. Returns a RunResult summary."""
        run_dir = self._get_run_dir()
        ensure_dir(run_dir)

        # Initialize database
        self._db = Database(self._config.storage.db_path)
        await self._db.init()

        # Record the crawl run
        await self._db.insert_crawl_run(
            {
                "id": self._run_id,
                "prompt": self._prompt,
                "crawl_plan_json": self._plan.model_dump_json(),
                "config_snapshot_json": self._config.model_dump_json(),
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
        )

        logger.info("run_started", run_id=self._run_id, prompt=self._prompt[:100])

        try:
            # Set up profile system (matcher + escalation)
            profile_matcher = None
            escalation_manager = None
            if self._enable_profiles:
                profile_matcher, escalation_manager = self._build_profile_system()

            # Run the crawler
            crawler = CrawlRunner(
                config=self._config,
                plan=self._plan,
                run_dir=run_dir,
                on_page_crawled=self._on_page_crawled,
                on_file_downloaded=self._on_file_downloaded,
                profile_matcher=profile_matcher,
                escalation_manager=escalation_manager,
            )

            crawl_result = await crawler.run()
            self._stats.pages_crawled = crawl_result.pages_crawled

            # Finalize
            await self._db.update_crawl_run(
                self._run_id,
                {
                    "status": "completed",
                    "finished_at": datetime.utcnow(),
                    "total_urls_fetched": self._stats.pages_crawled,
                    "total_documents_stored": self._stats.documents_stored,
                    "total_duplicates_found": self._stats.duplicates_found,
                    "total_errors": self._stats.errors,
                    "total_bytes_downloaded": self._stats.bytes_downloaded,
                    "crawlee_storage_dir": crawl_result.crawlee_storage_dir,
                },
            )

            logger.info(
                "run_completed",
                run_id=self._run_id,
                pages=self._stats.pages_crawled,
                documents=self._stats.documents_stored,
                duplicates=self._stats.duplicates_found,
                errors=self._stats.errors,
            )

        except Exception:
            logger.error("run_failed", run_id=self._run_id, exc_info=True)
            await self._db.update_crawl_run(
                self._run_id,
                {
                    "status": "failed",
                    "finished_at": datetime.utcnow(),
                    "total_errors": self._stats.errors + 1,
                },
            )
            raise
        finally:
            await self._db.close()

        return RunResult(
            run_id=self._run_id,
            pages_crawled=self._stats.pages_crawled,
            documents_stored=self._stats.documents_stored,
            duplicates_found=self._stats.duplicates_found,
            files_downloaded=self._stats.files_downloaded,
            errors=self._stats.errors,
            filtered_by_date=self._stats.filtered_by_date,
            no_date_detected=self._stats.no_date_detected,
        )

    async def _on_page_crawled(self, page_data: dict[str, Any]) -> None:
        """Callback: process a crawled HTML page through extraction + dedup + storage."""
        url = page_data.get("url", "")
        html = page_data.get("html", "")

        try:
            # Extract content
            result = self._html_extractor.extract(html, url=url)
            text = result.text
            meta = extract_metadata(result.metadata, text=text, url=url)

            if not text.strip():
                logger.debug("empty_extraction", url=url)
                return

            # Track pages with no detected date when a date range is active
            if self._has_date_range() and meta.published_date is None:
                self._stats.no_date_detected += 1

            # Date range filter
            if not self._is_in_date_range(meta.published_date):
                self._stats.filtered_by_date += 1
                logger.debug("outside_date_range", url=url, date=meta.published_date)
                return

            # Dedup check
            text_hash = content_hash_text(text)
            if self._dedup.check_exact(text_hash):
                self._stats.duplicates_found += 1
                logger.debug("exact_duplicate_skipped", url=url)
                return

            near_dup_id = self._dedup.check_near_duplicate(text)
            is_duplicate = near_dup_id is not None

            # Store raw HTML
            html_bytes = html.encode("utf-8")
            raw_hash = sha256_hex(html_bytes)
            raw_rel_path = content_addressed_path(raw_hash, "html")
            raw_abs_path = Path(self._config.storage.file_store_path) / "raw" / raw_rel_path
            ensure_dir(raw_abs_path.parent)
            raw_abs_path.write_bytes(html_bytes)

            # Build document record
            doc_id = str(uuid4())
            self._dedup.record_hash(text_hash)
            sh = self._dedup.record_simhash(doc_id, text)

            doc = {
                "id": doc_id,
                "crawl_run_id": self._run_id,
                "source_url": url,
                "canonical_url": normalize_url(url),
                "content_hash": text_hash,
                "simhash": sh,
                "content_type": page_data.get("content_type", "text/html"),
                "title": meta.title or result.title,
                "author": meta.author,
                "published_date": (
                    meta.published_date.isoformat() if meta.published_date else None
                ),
                "language": meta.language,
                "extracted_text": text[: self._config.extraction.max_text_length],
                "text_length": len(text),
                "raw_file_path": raw_rel_path,
                "file_size_bytes": len(html_bytes),
                "fetch_status": page_data.get("status_code", 200),
                "fetch_timestamp": datetime.utcnow(),
                "depth": 0,
                "parent_url": None,
                "metadata_json": json.dumps(meta.to_dict()),
                "is_duplicate": is_duplicate,
                "duplicate_of_id": near_dup_id,
                "created_at": datetime.utcnow(),
            }

            await self._db.insert_document(doc)
            self._stats.documents_stored += 1
            self._stats.bytes_downloaded += len(html_bytes)

            if is_duplicate:
                self._stats.duplicates_found += 1

        except Exception:
            self._stats.errors += 1
            logger.error("page_processing_failed", url=url, exc_info=True)

    async def _on_file_downloaded(
        self, result: DownloadResult, parent_url: str
    ) -> None:
        """Callback: process a downloaded file (PDF, DOCX, etc.)."""
        try:
            self._stats.files_downloaded += 1
            self._stats.bytes_downloaded += result.file_size

            # For PDFs, extract text via Mistral OCR / pdfplumber
            extracted_text = ""
            if "pdf" in result.content_type.lower():
                file_path = (
                    Path(self._config.storage.file_store_path) / "raw" / result.file_path
                )
                if file_path.exists():
                    pdf_result = await self._pdf_extractor.extract_from_file(file_path)
                    extracted_text = pdf_result.text

                    # Date range filter for PDF attachments
                    if extracted_text and self._has_date_range():
                        pdf_meta = extract_metadata({}, text=extracted_text)
                        if pdf_meta.published_date is None:
                            self._stats.no_date_detected += 1
                        if not self._is_in_date_range(pdf_meta.published_date):
                            self._stats.filtered_by_date += 1
                            logger.debug(
                                "attachment_outside_date_range",
                                url=result.url,
                                date=pdf_meta.published_date,
                            )
                            return

            # Store as attachment linked to the parent page's document
            # Find the parent document by URL
            parent_doc = await self._db.find_by_canonical_url(
                normalize_url(parent_url), crawl_run_id=self._run_id
            )
            parent_doc_id = parent_doc["id"] if parent_doc else ""

            attachment = {
                "id": str(uuid4()),
                "document_id": parent_doc_id,
                "url": result.url,
                "filename": (
                    result.file_path.rsplit("/", 1)[-1]
                    if "/" in result.file_path
                    else result.file_path
                ),
                "content_type": result.content_type,
                "content_hash": result.content_hash,
                "file_size_bytes": result.file_size,
                "raw_file_path": result.file_path,
                "extracted_text": extracted_text or None,
                "fetch_status": result.status_code,
                "created_at": datetime.utcnow(),
            }

            await self._db.insert_attachment(attachment)

            logger.info(
                "attachment_stored",
                url=result.url,
                parent_url=parent_url,
                content_type=result.content_type,
                has_text=bool(extracted_text),
            )

        except Exception:
            self._stats.errors += 1
            logger.error(
                "file_processing_failed", url=result.url, exc_info=True
            )

    def _has_date_range(self) -> bool:
        """Check if the crawl plan has any date range bounds set."""
        return self._plan.date_range_start is not None or self._plan.date_range_end is not None

    def _is_in_date_range(self, pub_date: date | None) -> bool:
        """Check whether a document's publication date falls within the plan's date range.

        Returns True (keep the document) when:
        - No date range is set on the plan
        - pub_date is None (don't discard pages just because we can't detect the date)
        - pub_date falls within [date_range_start, date_range_end] (inclusive, either bound optional)
        """
        if not self._has_date_range():
            return True
        if pub_date is None:
            return True
        if self._plan.date_range_start and pub_date < self._plan.date_range_start:
            return False
        if self._plan.date_range_end and pub_date > self._plan.date_range_end:
            return False
        return True

    def _build_profile_system(self):
        """Set up the profile matcher and escalation manager."""
        from webcollector.profiles.escalation import EscalationManager
        from webcollector.profiles.matcher import ProfileMatcher
        from webcollector.profiles.store import ProfileStore

        store = ProfileStore()
        store.load_all()
        matcher = ProfileMatcher(store)

        escalation = None
        if self._config.llm.api_key:
            escalation = EscalationManager(
                llm_config=self._config.llm,
                profile_store=store,
            )

        return matcher, escalation

    def _get_run_dir(self) -> Path:
        """Get the directory for this run's data."""
        base = Path(self._config.storage.file_store_path)
        return base / "runs" / self._run_id


class RunStats:
    """Mutable counters for tracking run progress."""

    def __init__(self) -> None:
        self.pages_crawled: int = 0
        self.documents_stored: int = 0
        self.duplicates_found: int = 0
        self.files_downloaded: int = 0
        self.errors: int = 0
        self.bytes_downloaded: int = 0
        self.filtered_by_date: int = 0
        self.no_date_detected: int = 0


class RunResult:
    """Summary of a completed run."""

    def __init__(
        self,
        run_id: str,
        pages_crawled: int = 0,
        documents_stored: int = 0,
        duplicates_found: int = 0,
        files_downloaded: int = 0,
        errors: int = 0,
        filtered_by_date: int = 0,
        no_date_detected: int = 0,
    ) -> None:
        self.run_id = run_id
        self.pages_crawled = pages_crawled
        self.documents_stored = documents_stored
        self.duplicates_found = duplicates_found
        self.files_downloaded = files_downloaded
        self.errors = errors
        self.filtered_by_date = filtered_by_date
        self.no_date_detected = no_date_detected
