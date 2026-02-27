"""Escalation manager — bridges crawler failures to Haiku for profile generation.

When the crawler encounters a page it can't handle (empty content, no links,
JS-navigation required), the EscalationManager sends page signals to Claude Haiku
to generate a new SiteProfile.  The profile is saved and applied to the current
and future crawls.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from webcollector.profiles.matcher import PageSignals
from webcollector.profiles.models import SiteProfile

if TYPE_CHECKING:
    from webcollector.config import LLMConfig
    from webcollector.profiles.store import ProfileStore

logger = structlog.get_logger(__name__)


_PROFILE_SYSTEM_PROMPT = """\
You are a web crawling expert.  Given signals extracted from a webpage that \
a crawler failed to handle, generate a site profile that describes how this \
type of site works.

A site profile is NOT specific to one URL — it describes a *category* of sites \
(e.g. "Clerkbase municipal platform", "WordPress blog", "Angular SPA with REST API").

## Input signals you will receive:
- URL of the page
- HTTP status code
- Content length (chars of extracted text)
- Meta generator tag (if present)
- Script sources found on the page
- Body/HTML CSS classes
- A snippet of the raw HTML (first ~5000 chars)
- The specific failure reason (empty content, no links found, etc.)

## Output format
Respond with a JSON object inside <site_profile> tags:

<site_profile>
{
  "name": "kebab-case-identifier",
  "description": "Human-readable description of this site type",
  "fingerprint": {
    "meta_generators": ["generator-name"],
    "script_patterns": ["pattern-in-script-src"],
    "body_class_patterns": ["pattern-in-body-class"],
    "url_patterns": ["regex-pattern"],
    "css_selectors": ["div.some-class"],
    "min_confidence": 2
  },
  "navigation": {
    "rendering_mode": "playwright_only",
    "js_interactions": [
      {"action": "click_all", "selector": ".tree-node", "timeout_ms": 3000}
    ],
    "listing_selectors": [".content-list a"],
    "pagination_selectors": [],
    "needs_js_for_links": true,
    "date_url_pattern": null
  },
  "content": {
    "content_selector": "article",
    "title_selector": "h1",
    "date_selector": ".published-date",
    "date_url_regex": null,
    "remove_selectors": ["nav", ".sidebar", "footer"],
    "has_attachments": false,
    "attachment_selector": null
  },
  "notes": "Explanation of why this profile was created and what makes this site type unique."
}
</site_profile>

## Guidelines
- Focus on the *type* of site, not the specific URL
- The fingerprint should match other sites built on the same platform
- Be specific with CSS selectors — use class names and IDs you see in the HTML
- If the site needs JavaScript rendering, set rendering_mode to "playwright_only"
- If the site has a folder/tree navigation, include click interactions for expanding nodes
- Keep min_confidence at 2+ to avoid false positives
"""


class EscalationManager:
    """Calls Claude Haiku to generate a SiteProfile when the crawler is stuck."""

    def __init__(
        self,
        llm_config: LLMConfig,
        profile_store: ProfileStore,
    ) -> None:
        self._llm_config = llm_config
        self._store = profile_store
        self._escalation_count = 0
        self._max_escalations = 5  # safety cap per crawl run

    @property
    def escalation_count(self) -> int:
        return self._escalation_count

    async def escalate(
        self,
        signals: PageSignals,
        failure_reason: str,
        status_code: int = 200,
        content_length: int = 0,
        html_snippet: str = "",
    ) -> SiteProfile | None:
        """Send page signals to Haiku and return a generated SiteProfile.

        Returns None if the LLM call fails or the response can't be parsed.
        """
        if self._escalation_count >= self._max_escalations:
            logger.warning(
                "escalation_cap_reached",
                count=self._escalation_count,
                max=self._max_escalations,
            )
            return None

        self._escalation_count += 1

        api_key = self._llm_config.api_key
        if not api_key:
            logger.error("escalation_no_api_key")
            return None

        # Build the user message with page signals
        user_message = self._build_message(
            signals, failure_reason, status_code, content_length, html_snippet
        )

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=self._llm_config.model,
                max_tokens=2048,
                temperature=0.0,
                system=_PROFILE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )

            # Extract text from response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            profile = self._parse_profile(text)
            if profile:
                self._store.save(profile)
                logger.info(
                    "profile_generated",
                    name=profile.name,
                    url=signals.url,
                    reason=failure_reason,
                )
            return profile

        except Exception:
            logger.error("escalation_failed", url=signals.url, exc_info=True)
            return None

    def _build_message(
        self,
        signals: PageSignals,
        failure_reason: str,
        status_code: int,
        content_length: int,
        html_snippet: str,
    ) -> str:
        parts = [
            f"The crawler failed to handle this page. Please generate a site profile.\n",
            f"**Failure reason:** {failure_reason}",
            f"**URL:** {signals.url}",
            f"**HTTP status:** {status_code}",
            f"**Extracted content length:** {content_length} chars",
        ]

        if signals.meta_generator:
            parts.append(f"**Meta generator:** {signals.meta_generator}")
        if signals.script_srcs:
            parts.append(f"**Script sources:** {', '.join(signals.script_srcs[:20])}")
        if signals.body_classes:
            parts.append(f"**Body/HTML classes:** {signals.body_classes}")

        if html_snippet:
            parts.append(f"\n**HTML snippet (first ~5000 chars):**\n```html\n{html_snippet[:5000]}\n```")

        return "\n".join(parts)

    def _parse_profile(self, text: str) -> SiteProfile | None:
        """Extract a SiteProfile from the LLM response text."""
        import re

        match = re.search(
            r"<site_profile>\s*(.*?)\s*</site_profile>",
            text,
            re.DOTALL,
        )
        if not match:
            logger.warning("escalation_no_profile_tags")
            return None

        try:
            data = json.loads(match.group(1))
            return SiteProfile(**data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("escalation_parse_failed", error=str(exc))
            return None
