"""Tests for interpreter tool executors (web_search, fetch_page, playwright_probe)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from webcollector.interpreter.tool_executors import (
    dispatch_tool,
    execute_fetch_page,
    execute_playwright_probe,
    execute_web_search,
)


class TestWebSearch:
    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_returns_results(self, mock_client_cls):
        html = """
        <html><body>
        <div class="result">
            <a class="result__a" href="https://example.com">Example Site</a>
            <a class="result__snippet">A great example site.</a>
        </div>
        <div class="result">
            <a class="result__a" href="https://other.com">Other Site</a>
            <a class="result__snippet">Another site.</a>
        </div>
        </body></html>
        """
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_web_search("test query", max_results=5)

        assert "Example Site" in result
        assert "Other Site" in result
        assert "Search results for 'test query'" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_handles_http_error(self, mock_client_cls):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = httpx.HTTPError("timeout")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

        result = await execute_web_search("test query")
        assert "Search failed" in result

    @pytest.mark.asyncio
    @patch("webcollector.interpreter.tool_executors.httpx.AsyncClient")
    async def test_no_results(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.text = "<html><body>No results</body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client_instance

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


def _make_playwright_mocks(rendered_html="<html><body>Content</body></html>"):
    """Build mock objects for the Playwright async context manager chain."""
    mock_page = AsyncMock()
    mock_page.content.return_value = rendered_html
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.click = AsyncMock()

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
