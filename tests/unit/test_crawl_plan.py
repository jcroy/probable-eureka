"""Tests for CrawlPlan model, including PaginationRule."""

from __future__ import annotations

import pytest

from webcollector.models.crawl_plan import (
    CrawlPlan,
    JsInteraction,
    JsInteractionStep,
    PaginationRule,
)


class TestPaginationRule:
    """Validate PaginationRule creation and constraints."""

    def test_url_parameter_pagination(self):
        rule = PaginationRule(
            strategy="url_parameter",
            url_template="https://example.com/search?start={offset}&count=100",
            param_start=0,
            param_step=100,
            param_max=400,
        )
        assert rule.strategy == "url_parameter"
        assert rule.url_template is not None
        assert "{offset}" in rule.url_template
        assert rule.param_step == 100
        assert rule.param_max == 400

    def test_next_selector_pagination(self):
        rule = PaginationRule(
            strategy="next_selector",
            next_selector="a.next-page",
            max_pages=50,
        )
        assert rule.strategy == "next_selector"
        assert rule.next_selector == "a.next-page"
        assert rule.max_pages == 50

    def test_url_parameter_requires_template(self):
        with pytest.raises(ValueError, match="url_parameter strategy requires url_template"):
            PaginationRule(strategy="url_parameter")

    def test_next_selector_requires_selector(self):
        with pytest.raises(ValueError, match="next_selector strategy requires next_selector"):
            PaginationRule(strategy="next_selector")

    def test_unknown_strategy_rejected(self):
        with pytest.raises(ValueError, match="Unknown pagination strategy"):
            PaginationRule(strategy="infinite_scroll")

    def test_defaults(self):
        rule = PaginationRule(
            strategy="url_parameter",
            url_template="https://example.com?p={offset}",
        )
        assert rule.param_start == 0
        assert rule.param_step == 1
        assert rule.param_max == 100
        assert rule.max_pages is None


class TestCrawlPlanWithPagination:
    """Test CrawlPlan integration with PaginationRule."""

    def test_crawl_plan_with_pagination(self):
        plan = CrawlPlan(
            intent_summary="Collect EDGAR filings",
            seed_urls=["https://efts.sec.gov/LATEST/search-index?q=*&start=0&count=100"],
            target_domains=["sec.gov"],
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="https://efts.sec.gov/LATEST/search-index?q=*&start={offset}&count=100",
                param_start=0,
                param_step=100,
                param_max=900,
            ),
        )
        assert plan.pagination is not None
        assert plan.pagination.strategy == "url_parameter"
        assert plan.pagination.param_max == 900

    def test_crawl_plan_without_pagination_backward_compat(self):
        plan = CrawlPlan(
            intent_summary="Simple crawl",
            seed_urls=["https://example.com"],
            target_domains=["example.com"],
        )
        assert plan.pagination is None

    def test_roundtrip_with_pagination(self):
        """Serialize and deserialize a plan with pagination."""
        plan = CrawlPlan(
            intent_summary="Paginated crawl",
            seed_urls=["https://example.com/page/1"],
            target_domains=["example.com"],
            pagination=PaginationRule(
                strategy="next_selector",
                next_selector=".pagination a.next",
                max_pages=20,
            ),
            js_interactions=[
                JsInteraction(
                    url_pattern=r"example\.com",
                    steps=[JsInteractionStep(action="click", selector=".btn")],
                )
            ],
        )
        data = plan.model_dump()
        restored = CrawlPlan(**data)
        assert restored.pagination is not None
        assert restored.pagination.strategy == "next_selector"
        assert restored.pagination.next_selector == ".pagination a.next"
        assert restored.pagination.max_pages == 20
        assert len(restored.js_interactions) == 1

    def test_json_includes_pagination(self):
        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="https://example.com?p={offset}",
                param_max=10,
            ),
        )
        json_str = plan.model_dump_json()
        assert "pagination" in json_str
        assert "url_parameter" in json_str

    def test_json_without_pagination(self):
        plan = CrawlPlan(
            intent_summary="test",
            seed_urls=["https://example.com"],
        )
        json_str = plan.model_dump_json()
        assert "pagination" in json_str  # field present, value is null
