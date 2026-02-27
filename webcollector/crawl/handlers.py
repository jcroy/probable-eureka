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
from webcollector.models.crawl_plan import CrawlPlan, JsInteraction
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
        self._js_interactions: list[tuple[re.Pattern, JsInteraction]] = [
            (re.compile(js.url_pattern), js) for js in plan.js_interactions
        ]
        self._pages_crawled = 0
        # Pagination link following state
        pagination = plan.pagination
        self._next_selector: str | None = (
            pagination.next_selector
            if pagination and pagination.strategy == "next_selector"
            else None
        )
        self._next_pages_followed = 0
        self._next_selector_max = (
            pagination.max_pages if pagination and pagination.max_pages else 1000
        )

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

        # Follow pagination links (next page)
        await self._enqueue_pagination_links(context, soup=soup)

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
            url = context.request.url
            await self._run_js_interactions(page, url)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            return html, soup

        return "", None

    async def _run_js_interactions(self, page, url: str) -> None:
        """Execute JS interaction steps for pages matching url_pattern."""
        for pattern, js_interaction in self._js_interactions:
            if not pattern.search(url):
                continue

            logger.info("running_js_interactions", url=url, pattern=js_interaction.url_pattern)

            for step in js_interaction.steps:
                try:
                    if step.action == "click" and step.selector:
                        await page.click(step.selector, timeout=step.timeout_ms)
                    elif step.action == "click_all" and step.selector:
                        elements = await page.query_selector_all(step.selector)
                        for el in elements:
                            await el.click()
                    elif step.action == "wait_for_selector" and step.selector:
                        await page.wait_for_selector(step.selector, timeout=step.timeout_ms)
                    elif step.action == "wait_for_timeout":
                        await page.wait_for_timeout(step.timeout_ms)
                    elif step.action == "scroll_to_bottom":
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(1000)
                except Exception as exc:
                    logger.warning(
                        "js_interaction_failed",
                        url=url,
                        action=step.action,
                        selector=step.selector,
                        error=str(exc),
                    )

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

    async def _enqueue_pagination_links(
        self, context: AdaptivePlaywrightCrawlingContext, soup=None
    ) -> None:
        """Find and enqueue "next page" links using multiple strategies."""
        if self._next_pages_followed >= self._next_selector_max:
            return

        if soup is None:
            soup = getattr(context, "soup", None)
        if not soup:
            return

        base_url = context.request.url
        next_url: str | None = None

        # Strategy 1: Explicit next_selector from pagination rule
        if self._next_selector:
            tag = soup.select_one(self._next_selector)
            if tag and tag.get("href"):
                next_url = resolve_url(base_url, tag["href"])

        # Strategy 2: Auto-detect <link rel="next"> and <a rel="next">
        if not next_url:
            for tag_name in ("link", "a"):
                tag = soup.find(tag_name, rel="next")
                if tag and tag.get("href"):
                    next_url = resolve_url(base_url, tag["href"])
                    break

        # Strategy 3: Heuristic CSS selectors for common pagination patterns
        if not next_url:
            heuristic_selectors = [
                'a[aria-label="Next"]',
                ".pagination .next a",
                ".pagination a.next",
                "a.pagination-next",
                "li.next a",
            ]
            for sel in heuristic_selectors:
                tag = soup.select_one(sel)
                if tag and tag.get("href"):
                    href = tag["href"]
                    if href and not href.startswith(("#", "javascript:")):
                        next_url = resolve_url(base_url, href)
                        break

        if not next_url:
            return

        # Scope check
        if not self._is_in_scope(next_url):
            logger.debug("pagination_link_out_of_scope", url=next_url)
            return

        from crawlee import Request

        self._next_pages_followed += 1
        logger.info(
            "pagination_next_page",
            url=next_url,
            page_num=self._next_pages_followed,
            from_url=base_url,
        )
        await context.add_requests(
            [Request.from_url(next_url, user_data={"depth": 0})]
        )

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
