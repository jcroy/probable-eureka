"""Tests for interpreter tool executors (web_search, fetch_page, playwright_probe)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from webcollector.interpreter.tool_executors import (
    _extract_interactive_elements,
    _format_interactive_elements,
    dispatch_tool,
    execute_fetch_page,
    execute_playwright_probe,
    execute_web_search,
)


class TestWebSearch:
    """Tests for execute_web_search with DDGS primary + HTML fallback."""

    @pytest.mark.asyncio
    async def test_ddgs_primary_returns_results(self):
        """When DDGS library works, returns results without hitting HTML endpoint."""
        fake_hits = [
            {"title": "Example", "href": "https://example.com", "body": "A site"},
            {"title": "Other", "href": "https://other.com", "body": "Another"},
        ]

        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = fake_hits
        mock_ddgs_instance.__enter__ = MagicMock(return_value=mock_ddgs_instance)
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_cls = MagicMock(return_value=mock_ddgs_instance)

        with patch.dict("sys.modules", {"duckduckgo_search": MagicMock(DDGS=mock_ddgs_cls)}):
            result = await execute_web_search("test query", max_results=5)

        assert "Example" in result
        assert "Other" in result
        assert "Search results for 'test query'" in result

    @pytest.mark.asyncio
    async def test_ddgs_failure_falls_back_to_html(self):
        """When DDGS raises an exception, falls back to HTML scraping."""
        # Make DDGS fail
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.side_effect = Exception("API down")
        mock_ddgs_instance.__enter__ = MagicMock(return_value=mock_ddgs_instance)
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)
        mock_ddgs_cls = MagicMock(return_value=mock_ddgs_instance)

        # Set up HTML fallback to succeed
        html = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com">Fallback Result</a>
            <a class="result__snippet">Found via HTML.</a>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.dict(
                "sys.modules", {"duckduckgo_search": MagicMock(DDGS=mock_ddgs_cls)}
            ),
            patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
            as mock_client_cls,
        ):
            mock_client_cls.return_value = mock_client_instance
            result = await execute_web_search("test query")

        assert "Fallback Result" in result

    @pytest.mark.asyncio
    async def test_ddgs_not_installed_falls_back(self):
        """When duckduckgo_search is not importable, falls back to HTML."""
        html = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com">HTML Result</a>
            <a class="result__snippet">Found via HTML.</a>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        # Remove duckduckgo_search from sys.modules so import fails
        import sys

        saved = sys.modules.pop("duckduckgo_search", None)
        try:
            with (
                patch.dict("sys.modules", {"duckduckgo_search": None}),
                patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
                as mock_client_cls,
            ):
                mock_client_cls.return_value = mock_client_instance
                result = await execute_web_search("test query")
        finally:
            if saved is not None:
                sys.modules["duckduckgo_search"] = saved

        assert "HTML Result" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_html_retry_on_202(self, mock_client_cls):
        """HTML fallback retries on 202 status and succeeds on second attempt."""
        # First call: 202 (empty)
        resp_202 = MagicMock()
        resp_202.status_code = 202

        # Second call: 200 with results
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.text = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com">Retry Result</a>
            <a class="result__snippet">Found after retry.</a>
        </div>
        </body></html>
        """
        resp_200.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = [resp_202, resp_200]
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        # Make DDGS unavailable so we hit HTML path
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            result = await execute_web_search("retry query")

        assert "Retry Result" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_handles_http_error(self, mock_client_cls):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = httpx.HTTPError("timeout")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            result = await execute_web_search("test query")
        assert "Search failed" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_no_results(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.text = "<html><body>No results</body></html>"
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            result = await execute_web_search("obscure query")
        assert "No results found" in result


class TestFetchPage:
    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_fetches_html_page(self, mock_client_cls):
        html = """
        <html><body>
        <h1>Hello World</h1>
        <p>Some content here.</p>
        <a href="/about">About</a>
        <a href="https://external.com">External</a>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_fetch_page("https://example.com")

        assert "Hello World" in result
        assert "Some content here" in result
        assert "Links found" in result
        assert "/about" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_handles_non_html(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.content = b"fake pdf bytes"
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_fetch_page("https://example.com/file.pdf")
        assert "Non-HTML response" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_without_links(self, mock_client_cls):
        html = "<html><body><p>Just text</p></body></html>"
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_fetch_page("https://example.com", extract_links=True)
        assert "No links found" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_http_error(self, mock_client_cls):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = httpx.HTTPError("connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_fetch_page("https://nonexistent.example.com")
        assert "Fetch failed" in result


def _make_playwright_mocks(
    rendered_html="<html><body>Content</body></html>",
    interactive_elements=None,
):
    """Build mock objects for the Playwright async context manager chain."""
    if interactive_elements is None:
        interactive_elements = []

    mock_page = AsyncMock()
    mock_page.content.return_value = rendered_html
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=interactive_elements)

    mock_browser = AsyncMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = AsyncMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

    # async_playwright() returns the context manager
    mock_async_playwright = MagicMock(return_value=mock_pw_ctx)

    return mock_async_playwright, mock_page, mock_browser


class TestPlaywrightProbe:
    @pytest.mark.asyncio
    async def test_renders_page(self):
        """Test that playwright_probe renders a page and extracts content."""
        rendered_html = """
        <html><body>
        <h1>Dynamic Content</h1>
        <a href="/page1">Page 1</a>
        <a href="/page2">Page 2</a>
        </body></html>
        """
        mock_async_pw, mock_page, _ = _make_playwright_mocks(rendered_html)

        # Mock the module-level import that happens inside the function
        mock_module = MagicMock()
        mock_module.async_playwright = mock_async_pw

        with patch.dict("sys.modules", {"playwright.async_api": mock_module}):
            result = await execute_playwright_probe("https://example.com")

        assert "Dynamic Content" in result
        assert "Page 1" in result

    @pytest.mark.asyncio
    async def test_handles_playwright_failure(self):
        """If playwright launch fails, return an error message."""
        mock_module = MagicMock()
        mock_pw_ctx = AsyncMock()
        mock_pw = AsyncMock()
        mock_pw.chromium.launch.side_effect = Exception("Browser launch failed")
        mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_module.async_playwright = MagicMock(return_value=mock_pw_ctx)

        with patch.dict("sys.modules", {"playwright.async_api": mock_module}):
            result = await execute_playwright_probe("https://example.com")

        assert "Playwright probe failed" in result

    @pytest.mark.asyncio
    async def test_executes_interactions(self):
        """Interaction steps (click, wait) are executed."""
        mock_async_pw, mock_page, _ = _make_playwright_mocks()
        mock_module = MagicMock()
        mock_module.async_playwright = mock_async_pw

        with patch.dict("sys.modules", {"playwright.async_api": mock_module}):
            result = await execute_playwright_probe(
                "https://example.com",
                interactions=[
                    {"action": "click", "selector": "text=Menu"},
                    {"action": "wait_for_timeout", "timeout_ms": 1000},
                ],
            )

        mock_page.click.assert_called_once_with("text=Menu", timeout=5000)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_includes_interactive_elements(self):
        """Interactive elements section appears in output with selectors."""
        elements = [
            {
                "selector": "#expand-btn",
                "text": "Expand All",
                "tag": "button",
                "role": "button",
                "expanded": None,
            },
            {
                "selector": "[aria-expanded='false']",
                "text": "2026 Minutes",
                "tag": "div",
                "role": "treeitem",
                "expanded": "false",
            },
        ]
        rendered_html = "<html><body><button id='expand-btn'>Expand All</button></body></html>"
        mock_async_pw, mock_page, _ = _make_playwright_mocks(
            rendered_html, interactive_elements=elements
        )
        mock_module = MagicMock()
        mock_module.async_playwright = mock_async_pw

        with patch.dict("sys.modules", {"playwright.async_api": mock_module}):
            result = await execute_playwright_probe("https://example.com")

        assert "Interactive elements" in result
        assert "#expand-btn" in result
        assert "Expand All" in result
        assert "treeitem" in result
        assert "expanded=false" in result

    @pytest.mark.asyncio
    async def test_no_interactive_elements_no_section(self):
        """When no interactive elements found, section is omitted."""
        mock_async_pw, mock_page, _ = _make_playwright_mocks(
            "<html><body><p>Static</p></body></html>",
            interactive_elements=[],
        )
        mock_module = MagicMock()
        mock_module.async_playwright = mock_async_pw

        with patch.dict("sys.modules", {"playwright.async_api": mock_module}):
            result = await execute_playwright_probe("https://example.com")

        assert "Interactive elements" not in result


class TestInteractiveElements:
    """Unit tests for interactive element extraction helpers."""

    @pytest.mark.asyncio
    async def test_extract_calls_evaluate(self):
        mock_page = AsyncMock()
        mock_page.evaluate.return_value = [
            {"selector": "#btn", "text": "Click", "tag": "button", "role": "", "expanded": None}
        ]
        result = await _extract_interactive_elements(mock_page)
        assert len(result) == 1
        assert result[0]["selector"] == "#btn"
        mock_page.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_handles_exception(self):
        mock_page = AsyncMock()
        mock_page.evaluate.side_effect = Exception("JS error")
        result = await _extract_interactive_elements(mock_page)
        assert result == []

    @pytest.mark.asyncio
    async def test_extract_limits_to_max(self):
        from webcollector.interpreter.tool_executors import _MAX_INTERACTIVE_ELEMENTS

        mock_page = AsyncMock()
        mock_page.evaluate.return_value = [
            {"selector": f"#el{i}", "text": f"El {i}", "tag": "button", "role": "", "expanded": None}
            for i in range(200)
        ]
        result = await _extract_interactive_elements(mock_page)
        assert len(result) == _MAX_INTERACTIVE_ELEMENTS

    def test_format_empty(self):
        assert _format_interactive_elements([]) == ""

    def test_format_elements(self):
        elements = [
            {"selector": "#btn", "text": "OK", "tag": "button", "role": "button", "expanded": None},
            {"selector": "[aria-expanded='false']", "text": "Folder", "tag": "div", "role": "treeitem", "expanded": "false"},
        ]
        output = _format_interactive_elements(elements)
        assert "Interactive elements (2)" in output
        assert "#btn" in output
        assert "role=button" in output
        assert "expanded=false" in output


class TestDispatchTool:
    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.execute_web_search")
    async def test_dispatches_web_search(self, mock_search):
        mock_search.return_value = "results"
        result = await dispatch_tool("web_search", {"query": "test", "max_results": 3})
        mock_search.assert_called_once_with(query="test", max_results=3)
        assert result == "results"

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.execute_fetch_page")
    async def test_dispatches_fetch_page(self, mock_fetch):
        mock_fetch.return_value = "page content"
        result = await dispatch_tool("fetch_page", {"url": "https://example.com"})
        mock_fetch.assert_called_once_with(url="https://example.com", extract_links=True)
        assert result == "page content"

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.execute_playwright_probe")
    async def test_dispatches_playwright_probe(self, mock_probe):
        mock_probe.return_value = "rendered"
        result = await dispatch_tool(
            "playwright_probe",
            {"url": "https://example.com", "interactions": []},
        )
        mock_probe.assert_called_once_with(
            url="https://example.com", interactions=[]
        )
        assert result == "rendered"

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await dispatch_tool("nonexistent_tool", {})
        assert "Unknown tool" in result
