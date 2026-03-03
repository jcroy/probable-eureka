"""Crawlee request handlers for webcollector.

These handlers are called by Crawlee's AdaptivePlaywrightCrawler for each URL.
They perform: rate limiting, content extraction, link discovery, and file downloads.
When a page yields low content, the profile system is consulted for site-specific
navigation hints, and may escalate to the LLM for unknown site types.
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

    from webcollector.profiles.escalation import EscalationManager
    from webcollector.profiles.matcher import ProfileMatcher
    from webcollector.profiles.models import SiteProfile

logger = structlog.get_logger(__name__)


def _safe_compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    """Compile regex patterns, skipping any that are invalid."""
    compiled: list[re.Pattern] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error:
            # LLM sometimes emits glob-style patterns — treat as literal
            compiled.append(re.compile(re.escape(p)))
            logger.warning("invalid_regex_escaped", pattern=p)
    return compiled


def _safe_compile_js(
    js: JsInteraction,
) -> tuple[re.Pattern, JsInteraction] | None:
    """Compile a JsInteraction's url_pattern, returning None on failure."""
    try:
        return (re.compile(js.url_pattern), js)
    except re.error:
        logger.warning("invalid_js_url_pattern", pattern=js.url_pattern)
        return None


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
        profile_matcher: ProfileMatcher | None = None,
        escalation_manager: EscalationManager | None = None,
    ) -> None:
        self._plan = plan
        self._rate_limiter = rate_limiter
        self._downloader = downloader
        self._on_page_crawled = on_page_crawled
        self._on_file_downloaded = on_file_downloaded
        self._profile_matcher = profile_matcher
        self._escalation_manager = escalation_manager
        # Shared mutable set: domains that should always use Playwright.
        # Passed to make_result_checker so httpx results for these domains
        # always fail, triggering Playwright re-fetch.
        self.force_playwright_domains: set[str] = set()
        self._include_patterns = _safe_compile_patterns(plan.url_patterns)
        self._exclude_patterns = _safe_compile_patterns(plan.exclude_patterns)
        self._js_interactions: list[tuple[re.Pattern, JsInteraction]] = [
            compiled
            for js in plan.js_interactions
            if (compiled := _safe_compile_js(js)) is not None
        ]
        self._pages_crawled = 0
        # Track domains where we've already checked/escalated profiles
        self._profiled_domains: dict[str, SiteProfile | None] = {}
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
        status_code = http_response.status_code if http_response else 200

        # If the page returned 403, the site likely blocks spoofed browser UAs.
        # Retry with a plain httpx request using an honest tool UA — many sites
        # (e.g. SEC.gov) allow simple declared automated tools but block
        # headless browsers.
        if status_code == 403:
            html, soup, status_code = await self._refetch_with_honest_ua(url)
            if status_code == 403:
                logger.warning("page_blocked_403", url=url)
                return

        # Check profiles on: first page of each domain, thin content, or errors
        domain = get_domain(url)
        first_for_domain = domain not in self._profiled_domains
        if first_for_domain or len(html) < 500 or status_code == 403:
            await self._check_profile(url, html, status_code)

        # Build page data for downstream processing
        page_data = {
            "url": url,
            "canonical_url": normalize_url(url),
            "status_code": status_code,
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

    async def _refetch_with_honest_ua(self, url: str):
        """Re-fetch a URL with a plain httpx request and honest UA.

        Sites like SEC.gov block browser-spoofed user agents but allow simple
        declared automated tools.  This is the last-resort fallback when both
        Crawlee's httpx path and Playwright path return 403.
        """
        import httpx
        from bs4 import BeautifulSoup

        honest_ua = "webcollector/1.0 (research tool)"
        try:
            async with httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": honest_ua},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and "html" in resp.headers.get(
                    "content-type", ""
                ):
                    try:
                        soup = BeautifulSoup(resp.text, "lxml")
                    except Exception as e:
                        logger.warning("soup_parse_failed", url=url, error=str(e))
                        return "", None, resp.status_code
                    logger.info("refetch_honest_ua_ok", url=url)
                    return resp.text, soup, 200
                return "", None, resp.status_code
        except httpx.HTTPError as exc:
            logger.warning("refetch_honest_ua_failed", url=url, error=str(exc))
            return "", None, 403

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
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception as e:
                logger.warning("soup_parse_failed", url=url, error=str(e))
                return html, None
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
        # Preserve current depth so pagination doesn't bypass max_depth
        current_depth = context.request.user_data.get("depth", 0)
        logger.info(
            "pagination_next_page",
            url=next_url,
            page_num=self._next_pages_followed,
            from_url=base_url,
            depth=current_depth,
        )
        await context.add_requests(
            [Request.from_url(next_url, user_data={"depth": current_depth})]
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

    async def _check_profile(self, url: str, html: str, status_code: int) -> None:
        """Check if a profile matches this page and apply navigation hints.

        Called when a page yields low content (< 200 chars of text).  Tries:
        1. Look up a matching profile from the profile store
        2. If no match, escalate to Haiku to generate a new profile
        3. Apply any discovered navigation hints (JS interactions, selectors)

        Results are cached per-domain so we only check/escalate once per site.
        """
        if not self._profile_matcher:
            return

        domain = get_domain(url)

        # Already checked this domain
        if domain in self._profiled_domains:
            return

        from webcollector.profiles.matcher import extract_signals

        signals = extract_signals(url, html)
        profile = self._profile_matcher.match(signals)

        # If no match, try LLM escalation
        if profile is None and self._escalation_manager:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html[:50_000], "lxml")
            text = soup.get_text(separator=" ", strip=True)

            profile = await self._escalation_manager.escalate(
                signals=signals,
                failure_reason=(
                    "empty_content" if len(text) < 200
                    else "low_content_quality"
                ),
                status_code=status_code,
                content_length=len(text),
                html_snippet=html[:5000],
            )

        self._profiled_domains[domain] = profile

        if profile:
            self._apply_profile_hints(profile, domain=domain)

    def _apply_profile_hints(self, profile: SiteProfile, domain: str = "") -> None:
        """Merge a profile's navigation hints into the current handler state."""
        nav = profile.navigation

        # Force Playwright for this domain if the profile demands it
        if nav.rendering_mode == "playwright_only" and domain:
            self.force_playwright_domains.add(domain)

        # Add JS interactions from the profile (avoid duplicates)
        for hint in nav.js_interactions:
            js = JsInteraction(
                url_pattern=".*",  # apply to all pages of this site
                steps=[
                    {
                        "action": hint.action,
                        "selector": hint.selector,
                        "timeout_ms": hint.timeout_ms,
                    }
                ],
            )
            compiled = _safe_compile_js(js)
            if compiled:
                self._js_interactions.append(compiled)

        # Add pagination selectors if we don't already have one
        if not self._next_selector and nav.pagination_selectors:
            self._next_selector = nav.pagination_selectors[0]

        logger.info(
            "profile_hints_applied",
            profile=profile.name,
            js_interactions=len(nav.js_interactions),
            pagination=bool(nav.pagination_selectors),
            forced_playwright=nav.rendering_mode == "playwright_only",
        )


def make_result_checker(
    min_text_length: int = 200,
    force_playwright_domains: set[str] | None = None,
):
    """Create a result checker for AdaptivePlaywrightCrawler.

    Returns a callable that checks whether a crawled page has meaningful content.
    If the httpx result fails this check, Crawlee will re-fetch with Playwright.

    The force_playwright_domains set is shared with CrawlHandlers — when a profile
    with rendering_mode="playwright_only" is matched, the domain is added to this
    set and all subsequent httpx results for that domain will fail the check,
    forcing Playwright rendering.
    """
    from crawlee.crawlers._adaptive_playwright._adaptive_playwright_crawler import (
        RequestHandlerRunResult,
    )

    forced = force_playwright_domains or set()

    def check_result(result: RequestHandlerRunResult) -> bool:
        # Check if the page yielded any data via push_data
        if not result.pushed_data:
            return False

        for item in result.pushed_data:
            html = item.get("html", "")
            if len(html) < min_text_length:
                return False

            # If this domain is marked as needing Playwright, fail the check
            # so Crawlee re-fetches with the browser
            url = item.get("url", "")
            if url and forced:
                domain = get_domain(url)
                if domain in forced:
                    return False

        return True

    return check_result
