"""Tests for JS interaction models and handler execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from webcollector.models.crawl_plan import (
    CrawlPlan,
    JsInteraction,
    JsInteractionStep,
)


class TestJsInteractionModels:
    """Test CrawlPlan serialization with JS interactions."""

    def test_step_defaults(self):
        step = JsInteractionStep(action="click", selector=".btn")
        assert step.action == "click"
        assert step.selector == ".btn"
        assert step.timeout_ms == 5000

    def test_step_custom_timeout(self):
        step = JsInteractionStep(action="wait_for_timeout", timeout_ms=3000)
        assert step.timeout_ms == 3000
        assert step.selector is None

    def test_interaction_with_steps(self):
        interaction = JsInteraction(
            url_pattern=r"example\.com/nav",
            steps=[
                JsInteractionStep(action="click", selector="text=Menu"),
                JsInteractionStep(action="wait_for_timeout", timeout_ms=2000),
            ],
        )
        assert interaction.url_pattern == r"example\.com/nav"
        assert len(interaction.steps) == 2

    def test_crawl_plan_with_js_interactions(self):
        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
            target_domains=["example.com"],
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[JsInteractionStep(action="click", selector=".nav-item")],
                )
            ],
        )
        assert len(plan.js_interactions) == 1
        assert plan.js_interactions[0].steps[0].selector == ".nav-item"

    def test_crawl_plan_without_js_interactions_backward_compat(self):
        """Existing plans without js_interactions still work."""
        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
            target_domains=["example.com"],
        )
        assert plan.js_interactions == []

    def test_roundtrip_json(self):
        """Serialize and deserialize a plan with JS interactions."""
        plan = CrawlPlan(
            intent_summary="Collect meeting minutes",
            seed_urls=["https://clerkshq.com/newport-ri"],
            target_domains=["clerkshq.com"],
            js_interactions=[
                JsInteraction(
                    url_pattern=r"clerkshq\.com/newport",
                    steps=[
                        JsInteractionStep(action="click", selector="text=Council"),
                        JsInteractionStep(action="wait_for_timeout", timeout_ms=2000),
                        JsInteractionStep(action="scroll_to_bottom"),
                    ],
                )
            ],
        )
        data = plan.model_dump()
        restored = CrawlPlan(**data)
        assert len(restored.js_interactions) == 1
        assert restored.js_interactions[0].steps[0].action == "click"
        assert restored.js_interactions[0].steps[1].timeout_ms == 2000
        assert restored.js_interactions[0].steps[2].action == "scroll_to_bottom"

    def test_json_serialization(self):
        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
            js_interactions=[
                JsInteraction(
                    url_pattern=".*",
                    steps=[JsInteractionStep(action="click", selector="#btn")],
                )
            ],
        )
        json_str = plan.model_dump_json()
        assert "js_interactions" in json_str
        assert "#btn" in json_str


class TestHandlerJsInteractions:
    """Test CrawlHandlers._run_js_interactions."""

    def _make_handlers(self, js_interactions=None):
        from webcollector.crawl.handlers import CrawlHandlers

        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
            target_domains=["example.com"],
            js_interactions=js_interactions or [],
        )
        rate_limiter = MagicMock()
        rate_limiter.acquire = AsyncMock()
        downloader = MagicMock()
        return CrawlHandlers(plan=plan, rate_limiter=rate_limiter, downloader=downloader)

    @pytest.mark.asyncio
    async def test_no_interactions_does_nothing(self):
        handlers = self._make_handlers()
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")
        page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_pattern_runs_steps(self):
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[
                        JsInteractionStep(action="click", selector=".nav-btn"),
                        JsInteractionStep(action="wait_for_timeout", timeout_ms=1000),
                    ],
                )
            ]
        )
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")

        page.click.assert_called_once_with(".nav-btn", timeout=5000)
        page.wait_for_timeout.assert_called_with(1000)

    @pytest.mark.asyncio
    async def test_non_matching_pattern_skipped(self):
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"other\.com",
                    steps=[
                        JsInteractionStep(action="click", selector=".btn"),
                    ],
                )
            ]
        )
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")
        page.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_click_all_action(self):
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[
                        JsInteractionStep(action="click_all", selector=".expand"),
                    ],
                )
            ]
        )
        el1 = AsyncMock()
        el2 = AsyncMock()
        page = AsyncMock()
        page.query_selector_all.return_value = [el1, el2]

        await handlers._run_js_interactions(page, "https://example.com/page")
        page.query_selector_all.assert_called_once_with(".expand")
        el1.click.assert_called_once()
        el2.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_wait_for_selector_action(self):
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[
                        JsInteractionStep(
                            action="wait_for_selector",
                            selector="#content",
                            timeout_ms=3000,
                        ),
                    ],
                )
            ]
        )
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")
        page.wait_for_selector.assert_called_once_with("#content", timeout=3000)

    @pytest.mark.asyncio
    async def test_scroll_to_bottom_action(self):
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[JsInteractionStep(action="scroll_to_bottom")],
                )
            ]
        )
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")
        page.evaluate.assert_called_once_with(
            "window.scrollTo(0, document.body.scrollHeight)"
        )

    @pytest.mark.asyncio
    async def test_failed_step_continues(self):
        """A failing step should not stop subsequent steps."""
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[
                        JsInteractionStep(action="click", selector=".missing"),
                        JsInteractionStep(action="wait_for_timeout", timeout_ms=500),
                    ],
                )
            ]
        )
        page = AsyncMock()
        page.click.side_effect = Exception("Element not found")

        await handlers._run_js_interactions(page, "https://example.com/page")
        # Second step should still run
        page.wait_for_timeout.assert_called_once_with(500)

    @pytest.mark.asyncio
    async def test_multiple_patterns_match(self):
        """When multiple patterns match, all interactions run."""
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[JsInteractionStep(action="click", selector=".first")],
                ),
                JsInteraction(
                    url_pattern=r"/page",
                    steps=[JsInteractionStep(action="click", selector=".second")],
                ),
            ]
        )
        page = AsyncMock()
        await handlers._run_js_interactions(page, "https://example.com/page")

        assert page.click.call_count == 2

    @pytest.mark.asyncio
    async def test_get_page_content_calls_js_interactions(self):
        """Playwright path calls _run_js_interactions before page.content()."""
        handlers = self._make_handlers(
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[JsInteractionStep(action="click", selector=".nav")],
                )
            ]
        )
        # Mock a Playwright-like context
        page = AsyncMock()
        page.content.return_value = "<html><body>Rendered</body></html>"

        context = MagicMock()
        context.soup = None  # no soup attribute → triggers Playwright path
        # Remove the soup attribute so getattr returns None
        del context.soup
        context.page = page
        context.request.url = "https://example.com/page"

        html, soup = await handlers._get_page_content(context)

        page.click.assert_called_once_with(".nav", timeout=5000)
        assert "Rendered" in html
