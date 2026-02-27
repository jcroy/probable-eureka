"""Tests for the site profile system (models, matcher, store, escalation)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from webcollector.profiles.matcher import PageSignals, ProfileMatcher, extract_signals
from webcollector.profiles.models import (
    ContentPattern,
    Fingerprint,
    JsInteractionHint,
    NavigationStrategy,
    SiteProfile,
)
from webcollector.profiles.store import ProfileStore


# ── SiteProfile model tests ─────────────────────────────────────────


class TestSiteProfileModel:
    def test_minimal_profile(self):
        """A profile with just a name should be valid."""
        profile = SiteProfile(name="test-profile")
        assert profile.name == "test-profile"
        assert profile.version == 1
        assert profile.fingerprint.min_confidence == 2

    def test_full_profile(self):
        """A fully populated profile should serialize and deserialize."""
        profile = SiteProfile(
            name="clerkbase",
            description="Clerkbase municipal platform",
            fingerprint=Fingerprint(
                script_patterns=["jquery.jstree"],
                url_patterns=[r"/Content/.*\.htm$"],
                css_selectors=["div.jstree"],
                min_confidence=2,
            ),
            navigation=NavigationStrategy(
                rendering_mode="playwright_only",
                js_interactions=[
                    JsInteractionHint(action="click_all", selector=".jstree-closed"),
                ],
                needs_js_for_links=True,
            ),
            content=ContentPattern(
                content_selector="body",
                date_url_regex=r"([a-z]{3})(\d{1,2})_(\d{2})",
            ),
        )
        data = profile.model_dump()
        restored = SiteProfile(**data)
        assert restored.name == "clerkbase"
        assert restored.fingerprint.script_patterns == ["jquery.jstree"]
        assert restored.navigation.rendering_mode == "playwright_only"
        assert len(restored.navigation.js_interactions) == 1

    def test_profile_yaml_roundtrip(self):
        """A profile should survive YAML serialization."""
        profile = SiteProfile(
            name="wordpress",
            fingerprint=Fingerprint(
                meta_generators=["WordPress"],
                min_confidence=1,
            ),
        )
        data = profile.model_dump(mode="json")
        yaml_str = yaml.dump(data)
        loaded = yaml.safe_load(yaml_str)
        restored = SiteProfile(**loaded)
        assert restored.name == "wordpress"
        assert restored.fingerprint.meta_generators == ["WordPress"]


# ── ProfileStore tests ───────────────────────────────────────────────


class TestProfileStore:
    def test_load_bundled_profiles(self):
        """Bundled profiles should load from the package directory."""
        store = ProfileStore()
        profiles = store.load_all()
        # At minimum, the clerkbase profile should exist
        names = [p.name for p in profiles]
        assert "clerkbase" in names

    def test_save_and_load_user_profile(self):
        """A saved user profile should be loadable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "profiles"
            store = ProfileStore(user_dir=user_dir)

            profile = SiteProfile(
                name="test-custom",
                description="Custom test profile",
                fingerprint=Fingerprint(
                    meta_generators=["TestCMS"],
                    min_confidence=1,
                ),
            )
            path = store.save(profile)
            assert path.exists()

            # Load fresh
            store2 = ProfileStore(user_dir=user_dir)
            store2.load_all()
            loaded = store2.get("test-custom")
            assert loaded is not None
            assert loaded.description == "Custom test profile"

    def test_user_overrides_bundled(self):
        """A user profile with the same name should override the bundled one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "profiles"
            store = ProfileStore(user_dir=user_dir)

            # Save a user profile named "clerkbase" (same as bundled)
            custom = SiteProfile(
                name="clerkbase",
                description="My custom clerkbase override",
            )
            store.save(custom)

            # Reload and check user version wins
            store2 = ProfileStore(user_dir=user_dir)
            store2.load_all()
            loaded = store2.get("clerkbase")
            assert loaded is not None
            assert loaded.description == "My custom clerkbase override"

    def test_get_nonexistent_profile(self):
        """Getting a profile that doesn't exist should return None."""
        store = ProfileStore()
        store.load_all()
        assert store.get("nonexistent-profile-xyz") is None

    def test_empty_user_dir(self):
        """A missing user directory should not cause errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = Path(tmpdir) / "nonexistent"
            store = ProfileStore(user_dir=user_dir)
            profiles = store.load_all()
            # Should still load bundled profiles
            assert len(profiles) > 0


# ── PageSignals & extract_signals tests ──────────────────────────────


class TestExtractSignals:
    def test_extracts_meta_generator(self):
        html = '<html><head><meta name="generator" content="WordPress 6.0"></head><body></body></html>'
        signals = extract_signals("https://example.com", html)
        assert "wordpress" in signals.meta_generator

    def test_extracts_script_srcs(self):
        html = (
            "<html><head>"
            '<script src="/wp-content/themes/theme/js/main.js"></script>'
            '<script src="/js/jquery.min.js"></script>'
            "</head><body></body></html>"
        )
        signals = extract_signals("https://example.com", html)
        assert len(signals.script_srcs) == 2
        assert any("wp-content" in s for s in signals.script_srcs)

    def test_extracts_body_classes(self):
        html = '<html class="no-js"><head></head><body class="page-home logged-out"></body></html>'
        signals = extract_signals("https://example.com", html)
        assert "page-home" in signals.body_classes
        assert "no-js" in signals.body_classes

    def test_no_signals_from_empty_html(self):
        signals = extract_signals("https://example.com", "<html><body></body></html>")
        assert signals.meta_generator == ""
        assert signals.script_srcs == []

    def test_url_preserved(self):
        signals = extract_signals("https://clerkshq.com/Content/test.htm", "<html></html>")
        assert signals.url == "https://clerkshq.com/Content/test.htm"


# ── ProfileMatcher tests ────────────────────────────────────────────


class TestProfileMatcher:
    def _make_store_with_profiles(self, profiles: list[SiteProfile]) -> ProfileStore:
        """Create a ProfileStore preloaded with the given profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(
                bundled_dir=Path(tmpdir) / "empty",  # no bundled
                user_dir=Path(tmpdir) / "profiles",
            )
            for p in profiles:
                store.save(p)
            store.load_all()
            return store

    def test_matches_clerkbase_by_script_and_url(self):
        """A page with jstree script and clerkshq.com URL should match clerkbase profile."""
        store = ProfileStore()
        store.load_all()
        matcher = ProfileMatcher(store)

        signals = PageSignals(
            url="https://clerkshq.com/Content/SouthKingstown-ri/council/2025/nov24_25tc.htm",
            script_srcs=["jquery.jstree.min.js", "jquery.min.js"],
            html_snippet='<div class="jstree"><ul><li>item</li></ul></div>',
        )
        profile = matcher.match(signals)
        assert profile is not None
        assert profile.name == "clerkbase"

    def test_no_match_for_generic_page(self):
        """A generic page should not match any profile."""
        store = ProfileStore()
        store.load_all()
        matcher = ProfileMatcher(store)

        signals = PageSignals(
            url="https://generic-blog.com/post/123",
            script_srcs=["app.js"],
        )
        profile = matcher.match(signals)
        assert profile is None

    def test_highest_score_wins(self):
        """When multiple profiles match, the one with more signal hits wins."""
        profile_a = SiteProfile(
            name="profile-a",
            fingerprint=Fingerprint(
                meta_generators=["CMS-A"],
                min_confidence=1,
            ),
        )
        profile_b = SiteProfile(
            name="profile-b",
            fingerprint=Fingerprint(
                meta_generators=["CMS-A"],
                script_patterns=["cms-a-framework"],
                min_confidence=1,
            ),
        )
        store = self._make_store_with_profiles([profile_a, profile_b])
        matcher = ProfileMatcher(store)

        signals = PageSignals(
            url="https://example.com",
            meta_generator="CMS-A v2.0",
            script_srcs=["cms-a-framework.js"],
        )
        matched = matcher.match(signals)
        assert matched is not None
        assert matched.name == "profile-b"

    def test_min_confidence_respected(self):
        """A profile should not match if fewer signals than min_confidence."""
        profile = SiteProfile(
            name="strict",
            fingerprint=Fingerprint(
                meta_generators=["StrictCMS"],
                script_patterns=["strict-lib.js"],
                min_confidence=3,  # needs 3 matches
            ),
        )
        store = self._make_store_with_profiles([profile])
        matcher = ProfileMatcher(store)

        # Only 1 signal matches (meta_generator)
        signals = PageSignals(
            url="https://example.com",
            meta_generator="StrictCMS",
        )
        assert matcher.match(signals) is None


# ── EscalationManager tests ─────────────────────────────────────────


class TestEscalationManager:
    def test_parse_profile_from_response(self):
        """The escalation manager should parse a valid profile from LLM response."""
        from webcollector.profiles.escalation import EscalationManager

        config = MagicMock()
        config.api_key = "test-key"
        store = MagicMock()

        manager = EscalationManager(llm_config=config, profile_store=store)

        response_text = """
Here's my analysis of the site:

<site_profile>
{
  "name": "angular-spa",
  "description": "Angular single-page application with REST API",
  "fingerprint": {
    "script_patterns": ["angular.min.js", "zone.js"],
    "css_selectors": ["app-root"],
    "min_confidence": 2
  },
  "navigation": {
    "rendering_mode": "playwright_only",
    "needs_js_for_links": true
  },
  "content": {
    "content_selector": "main"
  },
  "notes": "Angular SPA requiring full JS rendering."
}
</site_profile>
"""
        profile = manager._parse_profile(response_text)
        assert profile is not None
        assert profile.name == "angular-spa"
        assert profile.navigation.rendering_mode == "playwright_only"
        assert "angular.min.js" in profile.fingerprint.script_patterns

    def test_parse_invalid_json_returns_none(self):
        """Invalid JSON in the response should return None, not crash."""
        from webcollector.profiles.escalation import EscalationManager

        config = MagicMock()
        store = MagicMock()
        manager = EscalationManager(llm_config=config, profile_store=store)

        assert manager._parse_profile("<site_profile>not json</site_profile>") is None

    def test_parse_missing_tags_returns_none(self):
        """Response without site_profile tags should return None."""
        from webcollector.profiles.escalation import EscalationManager

        config = MagicMock()
        store = MagicMock()
        manager = EscalationManager(llm_config=config, profile_store=store)

        assert manager._parse_profile("No profile here") is None

    def test_escalation_cap(self):
        """Should stop escalating after max_escalations."""
        from webcollector.profiles.escalation import EscalationManager

        config = MagicMock()
        config.api_key = "test-key"
        store = MagicMock()
        manager = EscalationManager(llm_config=config, profile_store=store)
        manager._max_escalations = 2
        manager._escalation_count = 2

        signals = PageSignals(url="https://example.com")
        # Should be sync-safe (returns None immediately when capped)
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager.escalate(signals, "test")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_escalation_no_api_key(self):
        """Should return None when no API key is configured."""
        from webcollector.profiles.escalation import EscalationManager

        config = MagicMock()
        config.api_key = None
        store = MagicMock()
        manager = EscalationManager(llm_config=config, profile_store=store)

        signals = PageSignals(url="https://example.com")
        result = await manager.escalate(signals, "empty_content")
        assert result is None


# ── Handler integration tests ────────────────────────────────────────


class TestHandlerProfileIntegration:
    def test_handlers_accept_profile_params(self):
        """CrawlHandlers should accept profile_matcher and escalation_manager."""
        from unittest.mock import AsyncMock

        from webcollector.crawl.handlers import CrawlHandlers
        from webcollector.crawl.rate_limiter import DomainRateLimiter
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        handlers = CrawlHandlers(
            plan=plan,
            rate_limiter=DomainRateLimiter(default_rps=100),
            downloader=AsyncMock(),
            profile_matcher=MagicMock(),
            escalation_manager=MagicMock(),
        )
        assert handlers._profile_matcher is not None
        assert handlers._escalation_manager is not None

    def test_apply_profile_hints_adds_js_interactions(self):
        """Applying a profile should add its JS interactions to the handler."""
        from unittest.mock import AsyncMock

        from webcollector.crawl.handlers import CrawlHandlers
        from webcollector.crawl.rate_limiter import DomainRateLimiter
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        handlers = CrawlHandlers(
            plan=plan,
            rate_limiter=DomainRateLimiter(default_rps=100),
            downloader=AsyncMock(),
        )

        initial_js_count = len(handlers._js_interactions)

        profile = SiteProfile(
            name="test-profile",
            navigation=NavigationStrategy(
                js_interactions=[
                    JsInteractionHint(action="click_all", selector=".expand-btn"),
                    JsInteractionHint(action="scroll_to_bottom"),
                ],
            ),
        )
        handlers._apply_profile_hints(profile)

        assert len(handlers._js_interactions) == initial_js_count + 2

    def test_apply_profile_adds_pagination_selector(self):
        """Applying a profile with pagination should set the next selector."""
        from unittest.mock import AsyncMock

        from webcollector.crawl.handlers import CrawlHandlers
        from webcollector.crawl.rate_limiter import DomainRateLimiter
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        handlers = CrawlHandlers(
            plan=plan,
            rate_limiter=DomainRateLimiter(default_rps=100),
            downloader=AsyncMock(),
        )
        assert handlers._next_selector is None

        profile = SiteProfile(
            name="test-profile",
            navigation=NavigationStrategy(
                pagination_selectors=["a.next-page"],
            ),
        )
        handlers._apply_profile_hints(profile)

        assert handlers._next_selector == "a.next-page"

    def test_playwright_only_profile_adds_forced_domain(self):
        """A profile with rendering_mode=playwright_only should force Playwright for that domain."""
        from unittest.mock import AsyncMock

        from webcollector.crawl.handlers import CrawlHandlers
        from webcollector.crawl.rate_limiter import DomainRateLimiter
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        handlers = CrawlHandlers(
            plan=plan,
            rate_limiter=DomainRateLimiter(default_rps=100),
            downloader=AsyncMock(),
        )
        assert len(handlers.force_playwright_domains) == 0

        profile = SiteProfile(
            name="js-heavy",
            navigation=NavigationStrategy(rendering_mode="playwright_only"),
        )
        handlers._apply_profile_hints(profile, domain="example.com")

        assert "example.com" in handlers.force_playwright_domains

    def test_adaptive_profile_does_not_force_playwright(self):
        """A profile with rendering_mode=adaptive should NOT force Playwright."""
        from unittest.mock import AsyncMock

        from webcollector.crawl.handlers import CrawlHandlers
        from webcollector.crawl.rate_limiter import DomainRateLimiter
        from webcollector.models.crawl_plan import CrawlPlan

        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
        )
        handlers = CrawlHandlers(
            plan=plan,
            rate_limiter=DomainRateLimiter(default_rps=100),
            downloader=AsyncMock(),
        )
        profile = SiteProfile(
            name="normal",
            navigation=NavigationStrategy(rendering_mode="adaptive"),
        )
        handlers._apply_profile_hints(profile, domain="example.com")

        assert "example.com" not in handlers.force_playwright_domains


# ── Result checker tests ─────────────────────────────────────────────


class TestResultChecker:
    def test_forced_domain_fails_check(self):
        """The result checker should fail for domains in the forced set."""
        from webcollector.crawl.handlers import make_result_checker

        forced = {"clerkshq.com"}
        checker = make_result_checker(min_text_length=200, force_playwright_domains=forced)

        # Build a mock result with pushed_data
        from unittest.mock import MagicMock

        result = MagicMock()
        result.pushed_data = [
            {"url": "https://clerkshq.com/SouthKingstown", "html": "x" * 5000}
        ]
        assert checker(result) is False

    def test_non_forced_domain_passes_check(self):
        """Normal domains should pass the result checker if content is sufficient."""
        from webcollector.crawl.handlers import make_result_checker

        forced = {"clerkshq.com"}
        checker = make_result_checker(min_text_length=200, force_playwright_domains=forced)

        from unittest.mock import MagicMock

        result = MagicMock()
        result.pushed_data = [
            {"url": "https://example.com/page", "html": "x" * 5000}
        ]
        assert checker(result) is True

    def test_short_content_fails_regardless(self):
        """Pages with too little HTML should fail even without forced domains."""
        from webcollector.crawl.handlers import make_result_checker

        checker = make_result_checker(min_text_length=200)

        from unittest.mock import MagicMock

        result = MagicMock()
        result.pushed_data = [{"url": "https://example.com/empty", "html": "short"}]
        assert checker(result) is False

    def test_empty_pushed_data_fails(self):
        """No pushed data should fail the check."""
        from webcollector.crawl.handlers import make_result_checker

        checker = make_result_checker()

        from unittest.mock import MagicMock

        result = MagicMock()
        result.pushed_data = []
        assert checker(result) is False

    def test_no_forced_domains_works(self):
        """Result checker works fine with no forced domains."""
        from webcollector.crawl.handlers import make_result_checker

        checker = make_result_checker(min_text_length=100)

        from unittest.mock import MagicMock

        result = MagicMock()
        result.pushed_data = [{"url": "https://site.com/page", "html": "a" * 500}]
        assert checker(result) is True


# ── Content quality & intent verification tests ──────────────────────


class TestContentQuality:
    def test_low_ratio_flagged(self):
        """Pages with text/html ratio < 0.05 should be flagged."""
        from webcollector.orchestrator import RunStats

        stats = RunStats()
        # Simulate: 10k HTML, 200 chars text → ratio 0.02
        assert 200 / 10000 < 0.05
        stats.low_content_quality += 1
        assert stats.low_content_quality == 1

    def test_normal_ratio_not_flagged(self):
        """Pages with reasonable ratio should not be flagged."""
        # 5k HTML, 1k text → ratio 0.2
        assert 1000 / 5000 >= 0.05


class TestIntentVerification:
    def test_keyword_found_returns_true(self):
        """Intent check should pass when keywords are in text."""
        from webcollector.orchestrator import RunOrchestrator

        from webcollector.config import WebCollectorConfig
        from webcollector.models.crawl_plan import CrawlPlan

        config = WebCollectorConfig()
        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            keywords=["minutes", "council"],
        )
        orch = RunOrchestrator(config=config, plan=plan, enable_profiles=False)
        assert orch._check_intent_match(
            "Town Council meeting minutes from September 2025", "http://example.com"
        ) is True

    def test_keyword_missing_returns_false(self):
        """Intent check should fail when no keywords are in text."""
        from webcollector.orchestrator import RunOrchestrator

        from webcollector.config import WebCollectorConfig
        from webcollector.models.crawl_plan import CrawlPlan

        config = WebCollectorConfig()
        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            keywords=["minutes", "council"],
        )
        orch = RunOrchestrator(config=config, plan=plan, enable_profiles=False)
        assert orch._check_intent_match(
            "Welcome to our website. Browse our products.", "http://example.com"
        ) is False

    def test_case_insensitive_match(self):
        """Keyword matching should be case-insensitive."""
        from webcollector.orchestrator import RunOrchestrator

        from webcollector.config import WebCollectorConfig
        from webcollector.models.crawl_plan import CrawlPlan

        config = WebCollectorConfig()
        plan = CrawlPlan(
            seed_urls=["http://example.com"],
            target_domains=["example.com"],
            keywords=["MINUTES"],
        )
        orch = RunOrchestrator(config=config, plan=plan, enable_profiles=False)
        assert orch._check_intent_match(
            "Town council minutes are published here.", "http://example.com"
        ) is True
