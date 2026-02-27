"""Crawlee request handlers for webcollector.

These handlers are called by Crawlee's AdaptivePlaywrightCrawler for each URL.
They perform: rate limiting, content extraction, link discovery, and file downloads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from webcollector.crawl.downloader import FileDownloader
from webcollector.crawl.rate_limiter import DomainRateLimiter
from webcollector.models.crawl_plan import CrawlPlan
from webcollector.utils.url_utils import (
    get_domain,
    is_document_url,
    normalize_url,
    resolve_url,
    url_matches_patterns,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from crawlee.crawlers import AdaptivePlaywrightCrawlingContext

logger = structlog.get_logger(__name__)


class CrawlHandlers:
    """Encapsulates the request handler logic for a single crawl run.

    Created per crawl run with the run's CrawlPlan, rate limiter, and downloader.
    Provides the handler functions to register with Crawlee's router.
    """

    def __init__(
        self,
        plan: CrawlPlan,
        rate_limiter: DomainRateLimiter,
        downloader: FileDownloader,
        on_page_crawled: Callable | None = None,
        on_file_downloaded: Callable | None = None,
    ) -> None:
        self._plan = plan
        self._rate_limiter = rate_limiter
        self._downloader = downloader
        self._on_page_crawled = on_page_crawled
        self._on_file_downloaded = on_file_downloaded
        self._include_patterns = (
            [re.compile(p) for p in plan.url_patterns] if plan.url_patterns else []
        )
        self._exclude_patterns = (
            [re.compile(p) for p in plan.exclude_patterns] if plan.exclude_patterns else []
        )
        self._pages_crawled = 0

    @property
    def pages_crawled(self) -> int:
        return self._pages_crawled

    async def default_handler(self, context: AdaptivePlaywrightCrawlingContext) -> None:
        """Main request handler. Called for every crawled URL."""
        url = context.request.url
        domain = get_domain(url)

        # Per-domain rate limiting
        await self._rate_limiter.acquire(domain)

        self._pages_crawled += 1
        logger.info(
            "page_crawled",
            url=url,
            depth=context.request.user_data.get("depth", 0),
            pages_so_far=self._pages_crawled,
        )

        # Get HTML content — works for both httpx (soup) and Playwright (page) paths
        html, soup = await self._get_page_content(context)
        http_response = getattr(context, "http_response", None)

        # Build page data for downstream processing
        page_data = {
            "url": url,
            "canonical_url": normalize_url(url),
            "status_code": http_response.status_code if http_response else 200,
            "content_type": (
                http_response.headers.get("content-type", "text/html")
                if http_response
                else "text/html"
            ),
            "html": html,
            "headers": dict(http_response.headers) if http_response else {},
        }

        if self._on_page_crawled:
            await self._on_page_crawled(page_data)

        # Push to Crawlee's dataset for later retrieval
        await context.push_data(page_data)

        # Discover and enqueue links within scope
        await self._enqueue_scoped_links(context, soup=soup)

        # Find and download document attachments (PDFs, DOCX, etc.)
        await self._download_attachments(context, soup=soup)

    async def _get_page_content(self, context):
        """Extract HTML and BeautifulSoup from either httpx or Playwright context."""
        from bs4 import BeautifulSoup

        # httpx/BeautifulSoup path — context has .soup
        soup = getattr(context, "soup", None)
        if soup is not None:
            return str(soup), soup

        # Playwright path — context has .page
        page = getattr(context, "page", None)
        if page is not None:
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            return html, soup

        return "", None

    async def _enqueue_scoped_links(
        self, context: AdaptivePlaywrightCrawlingContext, soup=None
    ) -> None:
        """Extract links from the page and enqueue those matching the crawl plan scope."""
        if soup is None:
            soup = getattr(context, "soup", None)
        base_url = context.request.url

        if not soup:
            return

        # Collect candidate URLs from <a href> tags
        urls_to_enqueue: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            full_url = resolve_url(base_url, href)

            # Skip document URLs — those go through the downloader, not Crawlee
            if is_document_url(full_url):
                continue

            # Scope checks
            if not self._is_in_scope(full_url):
                continue

            urls_to_enqueue.append(full_url)

        if urls_to_enqueue:
            from crawlee import Request

            depth = context.request.user_data.get("depth", 0) + 1
            requests = [
                Request.from_url(u, user_data={"depth": depth})
                for u in urls_to_enqueue
            ]
            await context.add_requests(requests)
            logger.debug("links_enqueued", count=len(requests), from_url=base_url)

    async def _download_attachments(
        self, context: AdaptivePlaywrightCrawlingContext, soup=None
    ) -> None:
        """Find download links (PDFs, DOCX, etc.) and download them via our FileDownloader."""
        if soup is None:
            soup = getattr(context, "soup", None)
        base_url = context.request.url

        if not soup:
            return

        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if not href:
                continue

            full_url = resolve_url(base_url, href)

            if not is_document_url(full_url):
                continue

            if not self._is_in_scope(full_url):
                continue

            # Check if this document type is wanted
            if not self._is_wanted_doc_type(full_url):
                continue

            logger.info("downloading_attachment", url=full_url, parent=base_url)
            result = await self._downloader.download(full_url)

            if result and self._on_file_downloaded:
                await self._on_file_downloaded(result, base_url)

    def _is_in_scope(self, url: str) -> bool:
        """Check if a URL is within the crawl plan's scope."""
        plan = self._plan
        domain = get_domain(url)

        # Domain check
        if plan.target_domains:
            domain_match = any(
                domain == d or domain.endswith("." + d) for d in plan.target_domains
            )
            if not domain_match:
                return False

        # Exclude patterns
        if self._exclude_patterns and url_matches_patterns(url, plan.exclude_patterns):
            return False

        # Include patterns (if specified, URL must match at least one)
        if self._include_patterns:
            return url_matches_patterns(url, plan.url_patterns)

        return True

    def _is_wanted_doc_type(self, url: str) -> bool:
        """Check if the document type (by URL extension) matches the plan's desired types."""
        if not self._plan.document_types:
            return True

        url_lower = url.lower().rsplit("?", 1)[0]
        for doc_type in self._plan.document_types:
            if url_lower.endswith(f".{doc_type}"):
                return True

        return True  # If we can't determine type from URL, allow it


def make_result_checker(min_text_length: int = 200):
    """Create a result checker for AdaptivePlaywrightCrawler.

    Returns a callable that checks whether a crawled page has meaningful content.
    If the httpx result fails this check, Crawlee will re-fetch with Playwright.
    """
    from crawlee.crawlers._adaptive_playwright._adaptive_playwright_crawler import (
        RequestHandlerRunResult,
    )

    def check_result(result: RequestHandlerRunResult) -> bool:
        # Check if the page yielded any data via push_data
        if not result.pushed_data:
            return False

        for item in result.pushed_data:
            html = item.get("html", "")
            if len(html) < min_text_length:
                return False

        return True

    return check_result
