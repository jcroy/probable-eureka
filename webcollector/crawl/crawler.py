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
            "browser_launch_options": {
                "args": ["--no-sandbox", "--disable-setuid-sandbox"],
            },
            "browser_new_context_options": {
                "user_agent": self._config.crawl.user_agent,
            },
        }

    def _build_crawler(
        self,
        rendering_mode: str,
        max_requests: int,
        max_depth: int,
        concurrency: ConcurrencySettings,
        crawlee_config: Configuration,
    ):
        """Build the appropriate Crawlee crawler based on rendering mode.

        Modes:
          - "adaptive": AdaptivePlaywrightCrawler (httpx + Playwright fallback)
          - "http_only": BeautifulSoupCrawler (httpx only, no browser needed)
          - "playwright_only": PlaywrightCrawler (browser for every page)
        """
        shared_kwargs = {
            "max_request_retries": self._config.crawl.max_retries,
            "max_requests_per_crawl": max_requests,
            "max_crawl_depth": max_depth,
            "concurrency_settings": concurrency,
            "request_handler_timeout": timedelta(minutes=5),
            "configuration": crawlee_config,
            # Don't let Crawlee kill sessions on 403 — our handler retries
            # with an honest UA for sites that block browser-spoofed requests.
            "ignore_http_error_status_codes": [403],
        }

        if rendering_mode == "http_only":
            from crawlee.crawlers import BeautifulSoupCrawler

            logger.info("using_http_only_crawler")
            return BeautifulSoupCrawler(**shared_kwargs)

        # Default: adaptive (httpx + Playwright fallback)
        from crawlee.crawlers import AdaptivePlaywrightCrawler

        return AdaptivePlaywrightCrawler.with_beautifulsoup_static_parser(
            **shared_kwargs,
            respect_robots_txt_file=self._config.crawl.respect_robots_txt,
            result_checker=make_result_checker(min_text_length=200),
            playwright_crawler_specific_kwargs=self._build_playwright_kwargs(),
        )

    def _expand_pagination_seeds(self) -> list[str]:
        """Generate additional seed URLs from url_parameter pagination rules."""
        pagination = self._plan.pagination
        if not pagination or pagination.strategy != "url_parameter":
            return []

        template = pagination.url_template
        if not template or "{offset}" not in template:
            logger.warning(
                "pagination_template_missing_placeholder",
                template=template,
            )
            return []

        urls = []
        for offset in range(
            pagination.param_start, pagination.param_max + 1, pagination.param_step
        ):
            urls.append(template.format(offset=offset))
        return urls

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

        # Config takes precedence (CLI --max-pages sets config), then plan, then default
        config_max_pages = self._config.crawl.max_pages
        config_max_depth = self._config.crawl.max_depth
        plan_max_pages = self._plan.max_pages
        plan_max_depth = self._plan.max_depth

        # Use the smaller of config and plan values (CLI flag = hard cap)
        max_requests = min(
            config_max_pages,
            plan_max_pages if plan_max_pages else config_max_pages,
        )
        max_depth = min(
            config_max_depth,
            plan_max_depth if plan_max_depth else config_max_depth,
        )

        rendering_mode = self._config.browser.rendering_mode

        # Combine plan seed URLs with pagination-expanded URLs (deduped, order-preserving)
        pagination_urls = self._expand_pagination_seeds()
        seen: set[str] = set()
        all_seed_urls: list[str] = []
        for url in [*self._plan.seed_urls, *pagination_urls]:
            if url not in seen:
                seen.add(url)
                all_seed_urls.append(url)

        logger.info(
            "crawler_starting",
            seed_urls=len(all_seed_urls),
            pagination_urls=len(pagination_urls),
            max_requests=max_requests,
            max_depth=max_depth,
            max_concurrency=self._config.crawl.max_concurrency,
            max_tasks_per_minute=self._config.crawl.max_tasks_per_minute,
            rendering_mode=rendering_mode,
        )

        crawler = self._build_crawler(
            rendering_mode=rendering_mode,
            max_requests=max_requests,
            max_depth=max_depth,
            concurrency=concurrency,
            crawlee_config=crawlee_config,
        )

        # Register our handler
        @crawler.router.default_handler
        async def handle(context):
            await self._handlers.default_handler(context)

        # Build seed requests
        seed_requests = [
            Request.from_url(url, user_data={"depth": 0})
            for url in all_seed_urls
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
