"""Tests for pagination seed expansion (crawler) and link following (handlers)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from webcollector.crawl.handlers import CrawlHandlers
from webcollector.models.crawl_plan import CrawlPlan, PaginationRule


def _make_plan(**kwargs) -> CrawlPlan:
    defaults = {
        "intent_summary": "test",
        "seed_urls": ["https://example.com"],
        "target_domains": ["example.com"],
    }
    defaults.update(kwargs)
    return CrawlPlan(**defaults)


def _make_handlers(plan: CrawlPlan | None = None) -> CrawlHandlers:
    if plan is None:
        plan = _make_plan()
    rate_limiter = MagicMock()
    rate_limiter.acquire = AsyncMock()
    downloader = MagicMock()
    return CrawlHandlers(plan=plan, rate_limiter=rate_limiter, downloader=downloader)


def _make_context(url: str, html: str) -> MagicMock:
    """Build a mock crawling context with soup parsed from html."""
    soup = BeautifulSoup(html, "html.parser")
    context = MagicMock()
    context.request.url = url
    context.request.user_data = {"depth": 0}
    context.soup = soup
    context.add_requests = AsyncMock()
    return context


# ──────────────────────────────────────────────────────────────
# Seed expansion tests (CrawlRunner._expand_pagination_seeds)
# ──────────────────────────────────────────────────────────────

class TestSeedExpansion:
    """Test CrawlRunner._expand_pagination_seeds."""

    def _make_runner(self, plan: CrawlPlan):
        from webcollector.crawl.crawler import CrawlRunner

        config = MagicMock()
        return CrawlRunner(config=config, plan=plan, run_dir=MagicMock())

    def test_no_pagination_returns_empty(self):
        plan = _make_plan()
        runner = self._make_runner(plan)
        assert runner._expand_pagination_seeds() == []

    def test_url_parameter_expansion(self):
        plan = _make_plan(
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="https://example.com/search?start={offset}&count=100",
                param_start=0,
                param_step=100,
                param_max=400,
            ),
        )
        runner = self._make_runner(plan)
        urls = runner._expand_pagination_seeds()
        assert len(urls) == 5  # 0, 100, 200, 300, 400
        assert urls[0] == "https://example.com/search?start=0&count=100"
        assert urls[4] == "https://example.com/search?start=400&count=100"

    def test_next_selector_does_not_expand(self):
        plan = _make_plan(
            pagination=PaginationRule(
                strategy="next_selector",
                next_selector="a.next",
                max_pages=10,
            ),
        )
        runner = self._make_runner(plan)
        assert runner._expand_pagination_seeds() == []

    def test_missing_offset_placeholder_warns(self):
        plan = _make_plan(
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="https://example.com/search?page=1",  # no {offset}
            ),
        )
        runner = self._make_runner(plan)
        urls = runner._expand_pagination_seeds()
        assert urls == []


# ──────────────────────────────────────────────────────────────
# Pagination link following tests (CrawlHandlers._enqueue_pagination_links)
# ──────────────────────────────────────────────────────────────

class TestPaginationLinkFollowing:
    """Test CrawlHandlers._enqueue_pagination_links."""

    @pytest.mark.asyncio
    async def test_next_selector_follows_link(self):
        plan = _make_plan(
            pagination=PaginationRule(
                strategy="next_selector",
                next_selector="a.next-page",
                max_pages=10,
            ),
        )
        handlers = _make_handlers(plan)
        html = '<html><body><a class="next-page" href="/page/2">Next</a></body></html>'
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert len(requests) == 1
        assert requests[0].url == "https://example.com/page/2"

    @pytest.mark.asyncio
    async def test_auto_detect_link_rel_next(self):
        handlers = _make_handlers()
        html = '<html><head><link rel="next" href="/page/2"></head><body></body></html>'
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert requests[0].url == "https://example.com/page/2"

    @pytest.mark.asyncio
    async def test_auto_detect_a_rel_next(self):
        handlers = _make_handlers()
        html = '<html><body><a rel="next" href="/page/2">Next</a></body></html>'
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert requests[0].url == "https://example.com/page/2"

    @pytest.mark.asyncio
    async def test_auto_detect_aria_label_next(self):
        handlers = _make_handlers()
        html = '<html><body><a aria-label="Next" href="/page/2">→</a></body></html>'
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert requests[0].url == "https://example.com/page/2"

    @pytest.mark.asyncio
    async def test_no_pagination_no_enqueue(self):
        handlers = _make_handlers()
        html = "<html><body><p>No pagination here</p></body></html>"
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_pages_respected(self):
        plan = _make_plan(
            pagination=PaginationRule(
                strategy="next_selector",
                next_selector="a.next",
                max_pages=2,
            ),
        )
        handlers = _make_handlers(plan)
        html = '<html><body><a class="next" href="/page/N">Next</a></body></html>'

        # Follow two pages
        for i in range(2):
            context = _make_context(f"https://example.com/page/{i}", html)
            await handlers._enqueue_pagination_links(context, soup=context.soup)

        # Third should be blocked by max_pages
        context = _make_context("https://example.com/page/2", html)
        await handlers._enqueue_pagination_links(context, soup=context.soup)
        # The third call should NOT enqueue
        assert handlers._next_pages_followed == 2

    @pytest.mark.asyncio
    async def test_out_of_scope_pagination_ignored(self):
        plan = _make_plan(target_domains=["example.com"])
        handlers = _make_handlers(plan)
        html = '<html><body><a rel="next" href="https://other.com/page/2">Next</a></body></html>'
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination_css_heuristic_next_class(self):
        handlers = _make_handlers()
        html = (
            '<html><body>'
            '<div class="pagination"><a class="next" href="/page/2">Next</a></div>'
            '</body></html>'
        )
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert requests[0].url == "https://example.com/page/2"

    @pytest.mark.asyncio
    async def test_pagination_li_next_a(self):
        handlers = _make_handlers()
        html = (
            '<html><body>'
            '<ul><li class="next"><a href="/page/2">Next</a></li></ul>'
            '</body></html>'
        )
        context = _make_context("https://example.com/page/1", html)

        await handlers._enqueue_pagination_links(context, soup=context.soup)

        context.add_requests.assert_called_once()
        requests = context.add_requests.call_args[0][0]
        assert requests[0].url == "https://example.com/page/2"
