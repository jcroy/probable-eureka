"""Tests for the agentic LLM-based prompt interpreter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webcollector.config import LLMConfig
from webcollector.interpreter.prompt_interpreter import (
    _extract_crawl_plan,
    interpret_prompt,
)


def _make_llm_config(**overrides) -> LLMConfig:
    defaults = {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_research_turns": 15,
    }
    defaults.update(overrides)
    return LLMConfig(**defaults)


GOOD_PLAN_JSON = """{
  "intent_summary": "Collect Python documentation pages",
  "seed_urls": ["https://docs.python.org/3/"],
  "target_domains": ["docs.python.org"],
  "url_patterns": ["/3/library/", "/3/tutorial/"],
  "exclude_patterns": ["/3/distutils/"],
  "document_types": ["html"],
  "max_depth": 2,
  "max_pages": 50,
  "keywords": ["python", "standard library"]
}"""


def _make_text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(tool_id: str, name: str, input_data: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _make_response(content_blocks: list, stop_reason: str = "end_turn"):
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    return resp


class TestExtractCrawlPlan:
    def test_extracts_from_tags(self):
        text = f"Here is the plan:\n<crawl_plan>\n{GOOD_PLAN_JSON}\n</crawl_plan>"
        data = _extract_crawl_plan(text)
        assert data is not None
        assert data["seed_urls"] == ["https://docs.python.org/3/"]

    def test_extracts_raw_json_fallback(self):
        data = _extract_crawl_plan(GOOD_PLAN_JSON)
        assert data is not None
        assert data["seed_urls"] == ["https://docs.python.org/3/"]

    def test_extracts_from_markdown_fences(self):
        text = f"```json\n{GOOD_PLAN_JSON}\n```"
        data = _extract_crawl_plan(text)
        assert data is not None

    def test_returns_none_for_invalid_json(self):
        assert _extract_crawl_plan("not json at all {{{") is None

    def test_returns_none_for_json_without_seed_urls(self):
        assert _extract_crawl_plan('{"hello": "world"}') is None


class TestInterpretPrompt:
    """Test the agentic multi-turn loop."""

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_direct_plan_no_tools(self, mock_anthropic):
        """LLM outputs a plan directly without using tools (simple case)."""
        plan_text = f"<crawl_plan>\n{GOOD_PLAN_JSON}\n</crawl_plan>"
        response = _make_response([_make_text_block(plan_text)], stop_reason="end_turn")

        client = AsyncMock()
        client.messages.create.return_value = response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config()
        plan = await interpret_prompt("get python docs", config)

        assert plan.seed_urls == ["https://docs.python.org/3/"]
        assert plan.target_domains == ["docs.python.org"]
        assert plan.max_depth == 2
        assert plan.max_pages == 50
        assert "python" in plan.keywords

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.dispatch_tool")
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_multi_turn_with_tool_use(self, mock_anthropic, mock_dispatch):
        """LLM uses web_search tool first, then outputs plan."""
        # Turn 1: LLM calls web_search
        tool_block = _make_tool_use_block(
            "tool_1", "web_search", {"query": "python docs"}
        )
        turn1_response = _make_response([tool_block], stop_reason="tool_use")

        # Turn 2: LLM outputs plan
        plan_text = f"<crawl_plan>\n{GOOD_PLAN_JSON}\n</crawl_plan>"
        turn2_response = _make_response(
            [_make_text_block(plan_text)], stop_reason="end_turn"
        )

        client = AsyncMock()
        client.messages.create.side_effect = [turn1_response, turn2_response]
        mock_anthropic.AsyncAnthropic.return_value = client

        mock_dispatch.return_value = "Search results: docs.python.org"

        config = _make_llm_config()
        plan = await interpret_prompt("get python docs", config)

        assert plan.seed_urls == ["https://docs.python.org/3/"]
        mock_dispatch.assert_called_once_with("web_search", {"query": "python docs"})

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.dispatch_tool")
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_multiple_tool_calls_in_one_turn(self, mock_anthropic, mock_dispatch):
        """LLM calls multiple tools in a single response."""
        # Turn 1: two tool calls
        tool1 = _make_tool_use_block("t1", "web_search", {"query": "clerkbase"})
        tool2 = _make_tool_use_block("t2", "fetch_page", {"url": "https://example.com"})
        turn1_response = _make_response([tool1, tool2], stop_reason="tool_use")

        # Turn 2: plan
        plan_text = f"<crawl_plan>\n{GOOD_PLAN_JSON}\n</crawl_plan>"
        turn2_response = _make_response(
            [_make_text_block(plan_text)], stop_reason="end_turn"
        )

        client = AsyncMock()
        client.messages.create.side_effect = [turn1_response, turn2_response]
        mock_anthropic.AsyncAnthropic.return_value = client

        mock_dispatch.return_value = "some result"

        config = _make_llm_config()
        plan = await interpret_prompt("test", config)

        assert mock_dispatch.call_count == 2
        assert plan.seed_urls == ["https://docs.python.org/3/"]

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_exhausted_turns_raises(self, mock_anthropic):
        """Error when LLM never produces a plan within max turns."""
        # Every turn is just text with no plan
        no_plan_response = _make_response(
            [_make_text_block("I'm still thinking...")], stop_reason="end_turn"
        )

        client = AsyncMock()
        client.messages.create.return_value = no_plan_response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config(max_research_turns=2)
        with pytest.raises(RuntimeError, match="did not produce a crawl plan"):
            await interpret_prompt("something vague", config)

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_empty_seed_urls_raises(self, mock_anthropic):
        empty_plan = (
            '<crawl_plan>{"intent_summary": "test", '
            '"seed_urls": [], "target_domains": []}</crawl_plan>'
        )
        response = _make_response([_make_text_block(empty_plan)], stop_reason="end_turn")

        client = AsyncMock()
        client.messages.create.return_value = response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config()
        with pytest.raises(RuntimeError, match="no seed URLs"):
            await interpret_prompt("do something vague", config)

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        config = _make_llm_config(api_key_env="NONEXISTENT_KEY_VAR")
        with pytest.raises(RuntimeError, match="No API key found"):
            await interpret_prompt("get docs", config)

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_passes_tools_and_system_prompt(self, mock_anthropic):
        """Verify tools and system prompt are passed to the API call."""
        plan_text = f"<crawl_plan>\n{GOOD_PLAN_JSON}\n</crawl_plan>"
        response = _make_response([_make_text_block(plan_text)], stop_reason="end_turn")

        client = AsyncMock()
        client.messages.create.return_value = response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config()
        await interpret_prompt("get docs", config)

        call_kwargs = client.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 3
        tool_names = {t["name"] for t in call_kwargs["tools"]}
        assert tool_names == {"web_search", "fetch_page", "playwright_probe"}
        assert "system" in call_kwargs

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_plan_with_js_interactions(self, mock_anthropic):
        """LLM outputs a plan that includes js_interactions."""
        plan_json = """{
          "intent_summary": "Collect meeting minutes from Clerkbase",
          "seed_urls": ["https://www.clerkshq.com/newport-ri"],
          "target_domains": ["clerkshq.com"],
          "url_patterns": ["/Content/Newport-ri/"],
          "exclude_patterns": [],
          "document_types": ["html"],
          "max_depth": 3,
          "max_pages": 100,
          "keywords": ["meeting", "minutes", "2026"],
          "js_interactions": [
            {
              "url_pattern": "clerkshq\\\\.com/newport",
              "steps": [
                {"action": "click", "selector": "text=City Council Meetings"},
                {"action": "wait_for_timeout", "timeout_ms": 2000}
              ]
            }
          ]
        }"""
        plan_text = f"<crawl_plan>\n{plan_json}\n</crawl_plan>"
        response = _make_response([_make_text_block(plan_text)], stop_reason="end_turn")

        client = AsyncMock()
        client.messages.create.return_value = response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config()
        plan = await interpret_prompt("meeting minutes from clerkbase", config)

        assert len(plan.js_interactions) == 1
        assert plan.js_interactions[0].url_pattern == "clerkshq\\.com/newport"
        assert len(plan.js_interactions[0].steps) == 2
        assert plan.js_interactions[0].steps[0].action == "click"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-123"})
    @patch("webcollector.interpreter.prompt_interpreter.anthropic")
    async def test_extra_fields_ignored(self, mock_anthropic):
        """Pydantic ignores extra fields in the plan JSON."""
        plan_json = """{
          "intent_summary": "test",
          "seed_urls": ["https://example.com"],
          "target_domains": ["example.com"],
          "max_depth": 1,
          "max_pages": 10,
          "some_extra_field": "ignored",
          "confidence": 0.95
        }"""
        plan_text = f"<crawl_plan>\n{plan_json}\n</crawl_plan>"
        response = _make_response([_make_text_block(plan_text)], stop_reason="end_turn")

        client = AsyncMock()
        client.messages.create.return_value = response
        mock_anthropic.AsyncAnthropic.return_value = client

        config = _make_llm_config()
        plan = await interpret_prompt("test", config)
        assert plan.seed_urls == ["https://example.com"]
