"""Tests for critical bug fixes identified in the audit report.

Covers:
1. DomainRateLimiter lock creation (no defaultdict race)
2. Pagination seed URL expansion cap
3. URL parsing safety for malformed URLs
4. Config warning on malformed YAML
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from webcollector.config import load_config
from webcollector.crawl.rate_limiter import DomainRateLimiter
from webcollector.utils.url_utils import (
    get_domain,
    is_document_url,
    is_same_domain,
    normalize_url,
)


# ---------------------------------------------------------------------------
# 1. DomainRateLimiter — lock creation safety
# ---------------------------------------------------------------------------
class TestDomainRateLimiterLocks:
    """Verify locks are created on-demand inside the event loop, not via defaultdict."""

    async def test_locks_created_on_demand(self):
        limiter = DomainRateLimiter(default_rps=100.0)
        assert len(limiter._locks) == 0

        await limiter.acquire("example.com")
        assert "example.com" in limiter._locks
        assert isinstance(limiter._locks["example.com"], asyncio.Lock)

    async def test_same_lock_reused_for_domain(self):
        limiter = DomainRateLimiter(default_rps=100.0)
        await limiter.acquire("example.com")
        lock1 = limiter._locks["example.com"]
        await limiter.acquire("example.com")
        lock2 = limiter._locks["example.com"]
        assert lock1 is lock2

    async def test_different_locks_per_domain(self):
        limiter = DomainRateLimiter(default_rps=100.0)
        await limiter.acquire("a.com")
        await limiter.acquire("b.com")
        assert limiter._locks["a.com"] is not limiter._locks["b.com"]

    async def test_concurrent_acquire_same_domain(self):
        """Multiple concurrent acquires for the same domain should not crash."""
        limiter = DomainRateLimiter(default_rps=100.0)
        results = await asyncio.gather(
            limiter.acquire("example.com"),
            limiter.acquire("example.com"),
            limiter.acquire("example.com"),
        )
        assert len(results) == 3
        assert "example.com" in limiter._locks

    async def test_concurrent_acquire_different_domains(self):
        """Concurrent acquires for different domains create independent locks."""
        limiter = DomainRateLimiter(default_rps=100.0)
        domains = [f"domain-{i}.com" for i in range(20)]
        await asyncio.gather(*(limiter.acquire(d) for d in domains))
        assert len(limiter._locks) == 20

    async def test_get_lock_method(self):
        """_get_lock returns consistent locks for the same domain."""
        limiter = DomainRateLimiter()
        lock1 = limiter._get_lock("test.com")
        lock2 = limiter._get_lock("test.com")
        assert lock1 is lock2
        assert isinstance(lock1, asyncio.Lock)


# ---------------------------------------------------------------------------
# 2. Pagination seed URL expansion cap
# ---------------------------------------------------------------------------
class TestPaginationSeedCap:
    """Verify pagination URL generation is capped at max_pages."""

    def _make_runner(self, param_max: int, max_pages: int | None = None):
        """Create a CrawlRunner with a pagination plan."""
        from webcollector.config import CrawlConfig, WebCollectorConfig
        from webcollector.crawl.crawler import CrawlRunner
        from webcollector.models.crawl_plan import CrawlPlan, PaginationRule

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            max_pages=max_pages,
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="http://example.com?page={offset}",
                param_start=1,
                param_max=param_max,
                param_step=1,
            ),
        )
        config = WebCollectorConfig(
            crawl=CrawlConfig(max_pages=100),
        )
        return CrawlRunner(config=config, plan=plan, run_dir=Path("/tmp/test"))

    def test_small_pagination_not_capped(self):
        runner = self._make_runner(param_max=5, max_pages=1000)
        urls = runner._expand_pagination_seeds()
        assert len(urls) == 5

    def test_large_pagination_capped_by_plan_max_pages(self):
        runner = self._make_runner(param_max=1_000_000, max_pages=50)
        urls = runner._expand_pagination_seeds()
        assert len(urls) == 50

    def test_large_pagination_capped_by_default_max_pages(self):
        """When plan uses default max_pages (1000), that cap is applied."""
        from webcollector.config import CrawlConfig, WebCollectorConfig
        from webcollector.crawl.crawler import CrawlRunner
        from webcollector.models.crawl_plan import CrawlPlan, PaginationRule

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            # max_pages defaults to 1000
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="http://example.com?page={offset}",
                param_start=1,
                param_max=1_000_000,
                param_step=1,
            ),
        )
        config = WebCollectorConfig(crawl=CrawlConfig(max_pages=100))
        runner = CrawlRunner(config=config, plan=plan, run_dir=Path("/tmp/test"))
        urls = runner._expand_pagination_seeds()
        # Plan max_pages=1000 is used as the cap
        assert len(urls) == 1000

    def test_pagination_urls_are_correct(self):
        runner = self._make_runner(param_max=3, max_pages=1000)
        urls = runner._expand_pagination_seeds()
        assert urls == [
            "http://example.com?page=1",
            "http://example.com?page=2",
            "http://example.com?page=3",
        ]

    def test_no_pagination_returns_empty(self):
        from webcollector.config import WebCollectorConfig
        from webcollector.crawl.crawler import CrawlRunner
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        config = WebCollectorConfig()
        runner = CrawlRunner(config=config, plan=plan, run_dir=Path("/tmp/test"))
        assert runner._expand_pagination_seeds() == []

    def test_zero_step_uses_fallback(self):
        """A zero param_step should not cause infinite loop."""
        from webcollector.config import WebCollectorConfig
        from webcollector.crawl.crawler import CrawlRunner
        from webcollector.models.crawl_plan import CrawlPlan, PaginationRule

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            max_pages=10,
            pagination=PaginationRule(
                strategy="url_parameter",
                url_template="http://example.com?page={offset}",
                param_start=0,
                param_max=100,
                param_step=0,  # Would cause infinite loop without guard
            ),
        )
        config = WebCollectorConfig()
        runner = CrawlRunner(config=config, plan=plan, run_dir=Path("/tmp/test"))
        urls = runner._expand_pagination_seeds()
        # Should not hang; step=0 is guarded to 1
        assert len(urls) <= 10


# ---------------------------------------------------------------------------
# 3. URL parsing safety — malformed URLs
# ---------------------------------------------------------------------------
class TestUrlParsingSafety:
    """Verify URL utilities handle malformed URLs without crashing."""

    def test_get_domain_normal(self):
        assert get_domain("http://example.com/page") == "example.com"

    def test_get_domain_empty_string(self):
        assert get_domain("") == ""

    def test_get_domain_scheme_only(self):
        assert get_domain("http://") == ""

    def test_get_domain_malformed_ipv6(self):
        # This previously raised ValueError
        result = get_domain("http://[invalid-ipv6/page")
        assert isinstance(result, str)

    def test_get_domain_valid_ipv6(self):
        result = get_domain("http://[::1]/page")
        assert isinstance(result, str)

    def test_get_domain_data_url(self):
        result = get_domain("data:text/plain,hello")
        assert isinstance(result, str)

    def test_normalize_url_normal(self):
        result = normalize_url("HTTP://Example.COM/Page")
        assert result == "http://example.com/Page"

    def test_normalize_url_empty(self):
        result = normalize_url("")
        assert isinstance(result, str)

    def test_normalize_url_malformed_ipv6(self):
        # Should not raise ValueError
        result = normalize_url("http://[invalid-ipv6/page")
        assert isinstance(result, str)

    def test_is_document_url_malformed(self):
        # Should not raise
        result = is_document_url("http://[invalid-ipv6/file.pdf")
        assert isinstance(result, bool)

    def test_is_same_domain_empty_url(self):
        result = is_same_domain("", "example.com")
        assert result is False

    def test_is_same_domain_malformed_url(self):
        result = is_same_domain("http://[bad-ipv6/page", "example.com")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 4. Config warning on malformed YAML
# ---------------------------------------------------------------------------
class TestConfigMalformedYaml:
    """Verify that malformed YAML config files produce warnings."""

    def test_list_yaml_warns(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """A YAML file containing a list should warn and use defaults."""
        config_file = tmp_path / "webcollector.yaml"
        config_file.write_text("- item1\n- item2\n")

        with caplog.at_level(logging.WARNING):
            config = load_config(config_path=config_file)

        # Should use defaults
        assert config.crawl.max_pages == 1000
        # Should have logged a warning
        assert any("invalid format" in r.message for r in caplog.records)

    def test_string_yaml_warns(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """A YAML file containing a plain string should warn and use defaults."""
        config_file = tmp_path / "webcollector.yaml"
        config_file.write_text("just a string\n")

        with caplog.at_level(logging.WARNING):
            config = load_config(config_path=config_file)

        assert config.crawl.max_pages == 1000
        assert any("invalid format" in r.message for r in caplog.records)

    def test_integer_yaml_warns(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """A YAML file containing an integer should warn and use defaults."""
        config_file = tmp_path / "webcollector.yaml"
        config_file.write_text("42\n")

        with caplog.at_level(logging.WARNING):
            config = load_config(config_path=config_file)

        assert config.crawl.max_pages == 1000
        assert any("invalid format" in r.message for r in caplog.records)

    def test_empty_yaml_uses_defaults_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """An empty YAML file (parses to None) should silently use defaults."""
        config_file = tmp_path / "webcollector.yaml"
        config_file.write_text("")

        with caplog.at_level(logging.WARNING):
            config = load_config(config_path=config_file)

        assert config.crawl.max_pages == 1000
        # Empty file → None, should NOT warn (nothing invalid, just empty)
        assert not any("invalid format" in r.message for r in caplog.records)

    def test_valid_yaml_loads_normally(self, tmp_path: Path):
        """A valid YAML dict should load without warnings."""
        config_file = tmp_path / "webcollector.yaml"
        config_file.write_text("crawl:\n  max_pages: 500\n")

        config = load_config(config_path=config_file)
        assert config.crawl.max_pages == 500

    def test_missing_config_uses_defaults(self):
        """When no config file exists, defaults are used."""
        config = load_config(config_path=Path("/nonexistent/webcollector.yaml"))
        assert config.crawl.max_pages == 1000
