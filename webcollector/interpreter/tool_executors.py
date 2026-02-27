"""Execute research tools for the agentic prompt interpreter.

Each executor takes the tool input dict and returns a string result.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, urljoin

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

_HTTP_TIMEOUT = 15.0
_MAX_TEXT_CHARS = 16_000
_MAX_LINKS = 120
_MAX_INTERACTIVE_ELEMENTS = 80

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

_SEARCH_HTML_RETRIES = 3
_SEARCH_RETRY_BACKOFF = 1.5  # seconds, multiplied each attempt


async def _ddgs_search(query: str, max_results: int) -> str | None:
    """Primary search path using the ``duckduckgo-search`` library.

    Returns formatted results string, or *None* on failure so the caller can
    fall back to HTML scraping.
    """
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("duckduckgo_search_not_installed")
        return None

    def _run() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    try:
        hits = await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:
        logger.warning("ddgs_search_failed", error=str(exc))
        return None

    if not hits:
        return None

    results: list[str] = []
    for h in hits[:max_results]:
        title = h.get("title", "")
        link = h.get("href", "")
        snippet = h.get("body", "")
        if title or link:
            entry = f"- {title}\n  URL: {link}"
            if snippet:
                entry += f"\n  {snippet}"
            results.append(entry)

    if not results:
        return None

    return f"Search results for '{query}':\n\n" + "\n\n".join(results)


async def _html_search_with_retry(query: str, max_results: int) -> str:
    """Fallback: scrape DuckDuckGo HTML endpoint with retry for 202/empty."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    for attempt in range(1, _SEARCH_HTML_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(url)

                if resp.status_code == 202:
                    logger.debug(
                        "html_search_202_retry",
                        attempt=attempt,
                        max_attempts=_SEARCH_HTML_RETRIES,
                    )
                    if attempt < _SEARCH_HTML_RETRIES:
                        await asyncio.sleep(_SEARCH_RETRY_BACKOFF * attempt)
                        continue
                    return "Search returned no results (DuckDuckGo returned 202)."

                resp.raise_for_status()
        except httpx.HTTPError as exc:
            return f"Search failed: {exc}"

        soup = BeautifulSoup(resp.text, "lxml")
        results: list[str] = []

        for item in soup.select(".result")[:max_results]:
            title_tag = item.select_one(".result__title a, .result__a")
            snippet_tag = item.select_one(".result__snippet")
            link = ""
            title = ""
            snippet = ""

            if title_tag:
                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")
                if "uddg=" in str(href):
                    from urllib.parse import parse_qs, urlparse

                    parsed = urlparse(str(href))
                    qs = parse_qs(parsed.query)
                    link = qs.get("uddg", [str(href)])[0]
                else:
                    link = str(href)
            if snippet_tag:
                snippet = snippet_tag.get_text(strip=True)

            if title or link:
                entry = f"- {title}\n  URL: {link}"
                if snippet:
                    entry += f"\n  {snippet}"
                results.append(entry)

        if results:
            return f"Search results for '{query}':\n\n" + "\n\n".join(results)

        # Empty results — retry if we have attempts left
        if attempt < _SEARCH_HTML_RETRIES:
            logger.debug(
                "html_search_empty_retry",
                attempt=attempt,
                max_attempts=_SEARCH_HTML_RETRIES,
            )
            await asyncio.sleep(_SEARCH_RETRY_BACKOFF * attempt)
            continue

    return "No results found."


async def execute_web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return titles + URLs + snippets.

    Primary: ``duckduckgo-search`` library (DDGS API).
    Fallback: HTML scraping with retry for 202/empty responses.
    """
    max_results = min(max(max_results, 1), 10)

    # Primary: DDGS library
    result = await _ddgs_search(query, max_results)
    if result is not None:
        return result

    # Fallback: HTML scraping with retry
    logger.debug("falling_back_to_html_search")
    return await _html_search_with_retry(query, max_results)


# ---------------------------------------------------------------------------
# Fetch page (plain HTTP)
# ---------------------------------------------------------------------------


async def execute_fetch_page(url: str, extract_links: bool = True) -> str:
    """Fetch a page via HTTP and return text content + optionally links."""
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Fetch failed: {exc}"

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return (
            f"Non-HTML response (content-type: {content_type}). "
            f"Length: {len(resp.content)} bytes."
        )

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove script/style noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + "\n... [truncated]"

    parts = [f"Page: {url}\n\nText content:\n{text}"]

    if extract_links:
        links: list[str] = []
        seen: set[str] = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            full = urljoin(url, href)
            if full not in seen:
                seen.add(full)
                link_text = a_tag.get_text(strip=True)[:80]
                links.append(f"  {link_text} → {full}")
        if links:
            link_section = "\n".join(links[:_MAX_LINKS])
            if len(links) > _MAX_LINKS:
                link_section += f"\n  ... and {len(links) - _MAX_LINKS} more links"
            parts.append(f"\nLinks found ({len(links)}):\n{link_section}")
        else:
            parts.append("\nNo links found on this page.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Interactive element extraction (for playwright_probe)
# ---------------------------------------------------------------------------

_INTERACTIVE_JS = """
() => {
    const selectors = [
        'button', 'a[onclick]', '[role="treeitem"]', '[role="button"]',
        '[aria-expanded]', '[onclick]', '.folder', '.tree-node',
        'summary', 'details', '[data-toggle]', '[role="tab"]',
        '.expandable', '.collapsible', '.accordion-header',
        '[aria-haspopup]', '.nav-item', '.menu-item'
    ];
    const seen = new Set();
    const results = [];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (seen.has(el)) continue;
            seen.add(el);
            const tag = el.tagName.toLowerCase();
            const id = el.id;
            const role = el.getAttribute('role') || '';
            const ariaLabel = el.getAttribute('aria-label') || '';
            const ariaExpanded = el.getAttribute('aria-expanded');
            const testId = el.getAttribute('data-testid') || '';
            const text = (el.textContent || '').trim().slice(0, 60);
            const classes = el.className && typeof el.className === 'string'
                ? el.className.split(/\\s+/).slice(0, 3).join('.')
                : '';

            // Build a usable selector (priority: #id > [aria-label] > [data-testid] > tag.class)
            let cssSelector;
            if (id) {
                cssSelector = '#' + CSS.escape(id);
            } else if (ariaLabel) {
                cssSelector = `[aria-label="${ariaLabel.replace(/"/g, '\\\\"')}"]`;
            } else if (testId) {
                cssSelector = `[data-testid="${testId.replace(/"/g, '\\\\"')}"]`;
            } else if (classes) {
                cssSelector = tag + '.' + classes;
            } else if (text && text.length <= 40) {
                cssSelector = `text="${text}"`;
            } else {
                cssSelector = tag;
            }

            results.push({
                selector: cssSelector,
                text: text,
                tag: tag,
                role: role,
                expanded: ariaExpanded
            });
        }
    }
    return results;
}
"""


async def _extract_interactive_elements(page: object) -> list[dict]:
    """Run JS in the page to find interactive elements with usable selectors."""
    try:
        elements = await page.evaluate(_INTERACTIVE_JS)  # type: ignore[union-attr]
        return elements[:_MAX_INTERACTIVE_ELEMENTS] if elements else []
    except Exception as exc:
        logger.warning("interactive_element_extraction_failed", error=str(exc))
        return []


def _format_interactive_elements(elements: list[dict]) -> str:
    """Format interactive elements into a human-readable section."""
    if not elements:
        return ""

    lines = [f"\nInteractive elements ({len(elements)}):"]
    for el in elements:
        parts = [f"  [{el['tag']}]"]
        if el.get("role"):
            parts.append(f"role={el['role']}")
        if el.get("expanded") is not None:
            parts.append(f"expanded={el['expanded']}")
        if el.get("text"):
            parts.append(f'"{el["text"]}"')
        parts.append(f"→ {el['selector']}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Playwright probe
# ---------------------------------------------------------------------------


async def execute_playwright_probe(
    url: str,
    interactions: list[dict] | None = None,
) -> str:
    """Load a page in Playwright/Chromium, run interactions, return rendered content."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return (
            "Error: playwright is not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )

    interactions = interactions or []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            # Wait a bit for initial JS rendering
            await page.wait_for_timeout(2000)

            # Execute interaction steps
            for step in interactions:
                action = step.get("action", "")
                selector = step.get("selector")
                timeout_ms = step.get("timeout_ms", 5000)

                try:
                    if action == "click" and selector:
                        await page.click(selector, timeout=timeout_ms)
                    elif action == "click_all" and selector:
                        elements = await page.query_selector_all(selector)
                        for el in elements:
                            await el.click()
                    elif action == "wait_for_selector" and selector:
                        await page.wait_for_selector(selector, timeout=timeout_ms)
                    elif action == "wait_for_timeout":
                        await page.wait_for_timeout(timeout_ms)
                    elif action == "scroll_to_bottom":
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(1000)
                except Exception as exc:
                    logger.warning(
                        "playwright_interaction_failed",
                        action=action,
                        selector=selector,
                        error=str(exc),
                    )

            # Extract interactive elements before closing
            interactive_elements = await _extract_interactive_elements(page)

            # Extract rendered content
            html = await page.content()
            await browser.close()
    except Exception as exc:
        return f"Playwright probe failed: {exc}"

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + "\n... [truncated]"

    # Extract links
    links: list[str] = []
    seen: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(url, href)
        if full not in seen:
            seen.add(full)
            link_text = a_tag.get_text(strip=True)[:80]
            links.append(f"  {link_text} → {full}")

    parts = [f"Rendered page: {url}\n\nText content:\n{text}"]
    if links:
        link_section = "\n".join(links[:_MAX_LINKS])
        if len(links) > _MAX_LINKS:
            link_section += f"\n  ... and {len(links) - _MAX_LINKS} more links"
        parts.append(f"\nLinks found ({len(links)}):\n{link_section}")
    else:
        parts.append("\nNo links found on this page.")

    # Append interactive elements section
    interactive_section = _format_interactive_elements(interactive_elements)
    if interactive_section:
        parts.append(interactive_section)

    return "\n".join(parts)


async def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Route a tool call to the appropriate executor."""
    if tool_name == "web_search":
        return await execute_web_search(
            query=tool_input["query"],
            max_results=tool_input.get("max_results", 5),
        )
    elif tool_name == "fetch_page":
        return await execute_fetch_page(
            url=tool_input["url"],
            extract_links=tool_input.get("extract_links", True),
        )
    elif tool_name == "playwright_probe":
        return await execute_playwright_probe(
            url=tool_input["url"],
            interactions=tool_input.get("interactions", []),
        )
    else:
        return f"Unknown tool: {tool_name}"
