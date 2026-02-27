"""Crawlee AdaptivePlaywrightCrawler setup and configuration.

This module creates and configures the Crawlee crawler from our WebCollectorConfig
and CrawlPlan, wires up the request handlers, and provides the run() entry point.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from crawlee import ConcurrencySettings, Request
from crawlee.configuration import Configuration
from crawlee.crawlers import AdaptivePlaywrightCrawler

from webcollector.crawl.downloader import FileDownloader
from webcollector.crawl.handlers import CrawlHandlers, make_result_checker
from webcollector.crawl.rate_limiter import DomainRateLimiter

if TYPE_CHECKING:
    from collections.abc import Callable

    from webcollector.config import WebCollectorConfig
    from webcollector.models.crawl_plan import CrawlPlan

logger = structlog.get_logger(__name__)


class CrawlRunner:
    """Configures and runs a Crawlee-based crawl from a CrawlPlan."""

    def __init__(
        self,
        config: WebCollectorConfig,
        plan: CrawlPlan,
        run_dir: Path,
        on_page_crawled: Callable | None = None,
        on_file_downloaded: Callable | None = None,
    ) -> None:
        self._config = config
        self._plan = plan
        self._run_dir = run_dir
        self._on_page_crawled = on_page_crawled
        self._on_file_downloaded = on_file_downloaded
        self._handlers: CrawlHandlers | None = None
        self._downloader: FileDownloader | None = None

    def _build_rate_limiter(self) -> DomainRateLimiter:
        """Build per-domain rate limiter from config."""
        limiter = DomainRateLimiter(
            default_rps=self._config.crawl.default_rate_limit_rps,
        )
        for domain, override in self._config.crawl.domain_overrides.items():
            if override.rate_limit_rps is not None:
                limiter.set_domain_rps(domain, override.rate_limit_rps)
        return limiter

    def _build_crawlee_config(self) -> Configuration:
        """Build Crawlee's Configuration object."""
        storage_dir = str(self._run_dir / "crawlee_storage")
        return Configuration(
            storage_dir=storage_dir,
            purge_on_start=False,  # Preserve state for resume
        )

    def _build_concurrency_settings(self) -> ConcurrencySettings:
        """Build Crawlee's ConcurrencySettings from our config."""
        crawl = self._config.crawl
        return ConcurrencySettings(
            min_concurrency=crawl.min_concurrency,
            max_concurrency=crawl.max_concurrency,
            max_tasks_per_minute=crawl.max_tasks_per_minute,
            desired_concurrency=min(crawl.max_concurrency, 5),
        )

    def _build_playwright_kwargs(self) -> dict:
        """Build Playwright-specific kwargs for the adaptive crawler."""
        return {
            "headless": True,
            "browser_type": "chromium",
            "browser_new_context_options": {
                "user_agent": self._config.crawl.user_agent,
            },
        }

    async def run(self) -> CrawlResult:
        """Execute the crawl.

        Returns a CrawlResult with stats about what was crawled.
        """
        rate_limiter = self._build_rate_limiter()

        self._downloader = FileDownloader(
            store_dir=self._run_dir,
            rate_limiter=rate_limiter,
            user_agent=self._config.crawl.user_agent,
            timeout=self._config.crawl.download_timeout_seconds,
            max_retries=self._config.crawl.max_retries,
        )

        self._handlers = CrawlHandlers(
            plan=self._plan,
            rate_limiter=rate_limiter,
            downloader=self._downloader,
            on_page_crawled=self._on_page_crawled,
            on_file_downloaded=self._on_file_downloaded,
        )

        crawlee_config = self._build_crawlee_config()
        concurrency = self._build_concurrency_settings()

        # Use max_pages from plan (override) or config (default)
        max_requests = self._plan.max_pages or self._config.crawl.max_pages
        max_depth = self._plan.max_depth or self._config.crawl.max_depth

        logger.info(
            "crawler_starting",
            seed_urls=len(self._plan.seed_urls),
            max_requests=max_requests,
            max_depth=max_depth,
            max_concurrency=self._config.crawl.max_concurrency,
            max_tasks_per_minute=self._config.crawl.max_tasks_per_minute,
        )

        crawler = AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
            max_request_retries=self._config.crawl.max_retries,
            max_requests_per_crawl=max_requests,
            max_crawl_depth=max_depth,
            concurrency_settings=concurrency,
            request_handler_timeout=timedelta(minutes=5),
            respect_robots_txt_file=self._config.crawl.respect_robots_txt,
            configuration=crawlee_config,
            result_checker=make_result_checker(min_text_length=200),
            playwright_crawler_specific_kwargs=self._build_playwright_kwargs(),
        )

        # Register our handler
        @crawler.router.default_handler
        async def handle(context):
            await self._handlers.default_handler(context)

        # Build seed requests
        seed_requests = [
            Request.from_url(url, user_data={"depth": 0})
            for url in self._plan.seed_urls
        ]

        try:
            await crawler.run(seed_requests)
        finally:
            await self._downloader.close()

        pages_crawled = self._handlers.pages_crawled

        logger.info(
            "crawler_finished",
            pages_crawled=pages_crawled,
        )

        return CrawlResult(
            pages_crawled=pages_crawled,
            crawlee_storage_dir=str(self._run_dir / "crawlee_storage"),
        )


class CrawlResult:
    """Summary of a completed crawl."""

    def __init__(
        self,
        pages_crawled: int = 0,
        files_downloaded: int = 0,
        errors: int = 0,
        crawlee_storage_dir: str = "",
    ) -> None:
        self.pages_crawled = pages_crawled
        self.files_downloaded = files_downloaded
        self.errors = errors
        self.crawlee_storage_dir = crawlee_storage_dir
