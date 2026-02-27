"""Anthropic tool-use definitions for the agentic prompt interpreter."""

from __future__ import annotations

TOOL_WEB_SEARCH = {
    "name": "web_search",
    "description": (
        "Search the web using DuckDuckGo. Returns titles, URLs, and snippets for the "
        "top results. Use this to discover real domains, find specific pages, or verify "
        "that a URL/site exists."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (1-10).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

TOOL_FETCH_PAGE = {
    "name": "fetch_page",
    "description": (
        "Fetch a web page via HTTP GET and return its text content and links. "
        "Use this to inspect a page's structure, find navigation links, or check "
        "whether a URL returns useful content. Does NOT execute JavaScript."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "extract_links": {
                "type": "boolean",
                "description": "Whether to extract and return links from the page.",
                "default": True,
            },
        },
        "required": ["url"],
    },
}

TOOL_PLAYWRIGHT_PROBE = {
    "name": "playwright_probe",
    "description": (
        "Load a page in a real browser (Chromium), optionally perform JS interactions "
        "(click elements, wait for content), and return the rendered text and links. "
        "Use this when fetch_page returns little content (JS-heavy sites) or when you "
        "need to click through navigation to discover hidden links."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to load in the browser.",
            },
            "interactions": {
                "type": "array",
                "description": (
                    "Optional list of interactions to perform before extracting content."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "click",
                                "click_all",
                                "wait_for_selector",
                                "wait_for_timeout",
                                "scroll_to_bottom",
                            ],
                            "description": "The action to perform.",
                        },
                        "selector": {
                            "type": "string",
                            "description": (
                                "CSS/text selector (required for "
                                "click, click_all, wait_for_selector)."
                            ),
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "description": "Timeout in ms (for waits). Default: 5000.",
                            "default": 5000,
                        },
                    },
                    "required": ["action"],
                },
                "default": [],
            },
        },
        "required": ["url"],
    },
}

ALL_TOOLS = [TOOL_WEB_SEARCH, TOOL_FETCH_PAGE, TOOL_PLAYWRIGHT_PROBE]
