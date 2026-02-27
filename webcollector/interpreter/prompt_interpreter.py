"""Agentic LLM-based prompt interpreter: natural language → CrawlPlan.

Uses Anthropic's tool_use API to give the LLM research tools (web_search,
fetch_page, playwright_probe) so it can discover real URLs and site structures
before outputting a structured CrawlPlan.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import anthropic
import structlog

from webcollector.interpreter.tool_executors import dispatch_tool
from webcollector.interpreter.tools import ALL_TOOLS
from webcollector.models.crawl_plan import CrawlPlan

if TYPE_CHECKING:
    from webcollector.config import LLMConfig

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
You are a web crawling planner with research tools. Given a user's data \
collection request, research the web to discover real URLs and site structure, \
then produce a structured crawl plan.

## Research Phase
Use the provided tools to:
1. Search for the correct domain/URLs (don't guess — verify)
2. Fetch pages to understand site structure and navigation
3. Use playwright_probe for JS-heavy sites where fetch_page returns little content

## Output Phase
When you have gathered enough information, output your crawl plan inside \
<crawl_plan> tags as a JSON object:

<crawl_plan>
{
  "intent_summary": "Brief description of what will be collected",
  "seed_urls": ["https://verified-url.com/path"],
  "target_domains": ["verified-domain.com"],
  "url_patterns": ["/path-pattern/"],
  "exclude_patterns": ["/login", "/admin"],
  "document_types": ["html", "pdf"],
  "max_depth": 2,
  "max_pages": 50,
  "keywords": ["relevant", "terms"],
  "pagination": null,
  "js_interactions": [
    {
      "url_pattern": "regex-for-matching-pages",
      "steps": [
        {"action": "click", "selector": "text=Menu Item", "timeout_ms": 5000},
        {"action": "wait_for_timeout", "timeout_ms": 2000}
      ]
    }
  ]
}
</crawl_plan>

## Guidelines
- ALWAYS use web_search first to find the correct domain — never guess URLs
- Use fetch_page to verify URLs exist and inspect page structure
- Use playwright_probe when fetch_page returns sparse content (JS-heavy sites)
- js_interactions: include ONLY if the site requires clicking through JS navigation \
to reveal content links. Most sites don't need this.
- seed_urls: must be verified, real URLs you have confirmed exist
- target_domains: restrict crawling to relevant domains only
- url_patterns: regex patterns to focus on the right sections
- exclude_patterns: skip login, admin, user profile, cart pages
- Set max_depth and max_pages conservatively — prefer focused crawls
- For simple/well-known sites, you may skip research and output the plan directly
- js_interactions action values: click, click_all, wait_for_selector, \
wait_for_timeout, scroll_to_bottom

## Detecting and using pagination
Many sites spread results across multiple pages using URL parameters or "Next" \
links. Use the `pagination` field instead of manually listing every page URL.

### URL parameter pagination
When you see URL parameters like `?start=`, `?page=`, `?offset=`, `&from=`:
1. Fetch the first page and look for the pagination pattern in links
2. Determine the parameter name, step size, and total range
3. Use `pagination` with `strategy: "url_parameter"`:

```json
"pagination": {
  "strategy": "url_parameter",
  "url_template": "https://efts.sec.gov/LATEST/search-index?q=*&start={offset}&count=100",
  "param_start": 0,
  "param_step": 100,
  "param_max": 900
}
```

The crawler will automatically generate seed URLs for every offset value.

### Next-page link pagination
When the site has a clickable "Next" button/link with a CSS selector:
```json
"pagination": {
  "strategy": "next_selector",
  "next_selector": "a.next-page",
  "max_pages": 50
}
```

The crawler will follow the link on each page automatically.

### Auto-detected pagination
The crawler also auto-detects `<link rel="next">`, `<a rel="next">`, and \
common patterns like `a[aria-label="Next"]`, `.pagination .next a`. You do NOT \
need to specify these — they are followed automatically.

**IMPORTANT**: NEVER list every page URL in `seed_urls`. If you see pagination \
parameters, use a `pagination` block. Only put the first/entry page in `seed_urls`.

## Navigating JS folder trees / hierarchical sites (e.g. ClerkBase, Laserfiche)
Many government document sites use JavaScript folder trees that hide content \
behind expandable nodes. To handle these:

1. **Probe first**: Use `playwright_probe` on the root page to see the initial \
structure. Check the "Interactive elements" section for CSS selectors.
2. **Map interactive elements to js_interactions**: The `playwright_probe` output \
shows interactive elements with their selectors and attributes. Convert them \
directly:
   - Elements with `expanded=false` or `aria-expanded="false"` → \
`{"action": "click_all", "selector": "[aria-expanded='false']"}`
   - Elements with `data-*` attributes → use the `data-*` attribute as the selector \
(e.g. `a[data-node-id="123"]` → `{"action": "click", "selector": "a[data-node-id='123']"}`)
   - Always add `{"action": "wait_for_timeout", "timeout_ms": 2000}` after click actions
3. **Multi-level trees**: Chain multiple expand + wait steps. After expanding \
top-level folders, sub-folders may appear that also need expanding.
4. **Verify with probe**: Use `playwright_probe` with candidate interactions \
to confirm they reveal the expected content before including in the plan.

Example js_interactions for a folder tree:
```json
"js_interactions": [
  {
    "url_pattern": ".*clerkbase\\.com.*",
    "steps": [
      {"action": "click_all", "selector": "[aria-expanded='false']", "timeout_ms": 3000},
      {"action": "wait_for_timeout", "timeout_ms": 2000},
      {"action": "click_all", "selector": "[aria-expanded='false']", "timeout_ms": 3000},
      {"action": "wait_for_timeout", "timeout_ms": 2000}
    ]
  }
]
```

## Recognising AJAX navigation links
Many sites use links with `href=""` or `href="#"` and `data-*` attributes for \
JS-driven navigation (folder trees, AJAX tabs, document viewers). These appear \
in two places in playwright_probe output:

1. **Links section**: Shown as `[JS-nav]` with their `data-*` attributes. \
These links CANNOT be fetched — they must be clicked via js_interactions.
2. **Interactive elements section**: `data-*` attributes are shown after the \
selector (e.g. `data: data-toc-url=/Content/Council data-level=1`). These \
reveal the site's navigation structure.

When building selectors for js_interactions, prefer `data-*` attribute \
selectors (e.g. `a[data-toc-url="/Content/Council"]`) over class-based ones — \
they are more stable across page updates.

## Pattern recognition — choosing the right strategy

- Search results/listings: URL has ?page=/?start= → `url_parameter` pagination
- Blog/news archive: "Next"/"Older" link → `next_selector` or auto-detect
- Government doc portal: Folder tree, [JS-nav] → `js_interactions`
- API-backed listing (EDGAR): URL params, count → `url_parameter` pagination
- Static docs: Hierarchical URLs, no JS → `seed_urls` + `url_patterns` only

Output ONLY the <crawl_plan> tags with JSON when you are ready. \
Do not add explanation outside the tags."""


def _extract_crawl_plan(text: str) -> dict | None:
    """Extract JSON from <crawl_plan>...</crawl_plan> tags."""
    match = re.search(r"<crawl_plan>\s*(\{.*?\})\s*</crawl_plan>", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    # Fallback: try raw JSON (no tags)
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "seed_urls" in data:
            return data
    except json.JSONDecodeError:
        pass

    return None


async def interpret_prompt(prompt: str, llm_config: LLMConfig) -> CrawlPlan:
    """Send the user prompt through an agentic research loop, then parse into CrawlPlan."""
    api_key = llm_config.api_key
    if not api_key:
        raise RuntimeError(
            f"No API key found. Set the {llm_config.api_key_env} environment variable."
        )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    max_turns = llm_config.max_research_turns

    logger.info(
        "interpreting_prompt",
        prompt=prompt[:100],
        model=llm_config.model,
        max_turns=max_turns,
    )

    messages: list[dict] = [{"role": "user", "content": prompt}]

    for turn in range(max_turns):
        logger.debug("interpreter_turn", turn=turn + 1, max_turns=max_turns)

        response = await client.messages.create(
            model=llm_config.model,
            max_tokens=llm_config.max_tokens,
            temperature=llm_config.temperature,
            system=SYSTEM_PROMPT,
            tools=ALL_TOOLS,
            messages=messages,
        )

        # Check for tool use vs final text
        if response.stop_reason == "tool_use":
            # Process all tool calls in this response
            tool_results = []
            assistant_content = response.content

            for block in assistant_content:
                if block.type == "tool_use":
                    logger.info(
                        "tool_call",
                        turn=turn + 1,
                        tool=block.name,
                        input=_summarize_input(block.input),
                    )
                    result = await dispatch_tool(block.name, block.input)
                    logger.debug("tool_result", tool=block.name, result_len=len(result))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            # Append the assistant message and tool results
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # stop_reason == "end_turn" — look for the crawl plan in text
        full_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                full_text += block.text

        plan_data = _extract_crawl_plan(full_text)
        if plan_data is not None:
            return _build_plan(plan_data)

        # No plan found yet — maybe the model wants to continue talking
        # Append and let it try again
        messages.append({"role": "assistant", "content": response.content})
        messages.append(
            {
                "role": "user",
                "content": "Please output your crawl plan now inside <crawl_plan> tags.",
            }
        )

    # Exhausted turns — try to extract from the last response
    raise RuntimeError(
        f"Interpreter did not produce a crawl plan within {max_turns} turns. "
        "Try a more specific prompt."
    )


def _build_plan(data: dict) -> CrawlPlan:
    """Validate and build a CrawlPlan from parsed JSON data."""
    # LLMs often output explicit null for optional/list fields — strip them
    # so Pydantic uses the field defaults instead of rejecting None.
    cleaned = {k: v for k, v in data.items() if v is not None}
    plan = CrawlPlan(**cleaned)

    if not plan.seed_urls:
        raise RuntimeError(
            "LLM generated a plan with no seed URLs. "
            "Try a more specific prompt, e.g. include the website name or URL."
        )

    logger.info(
        "plan_generated",
        seed_urls=len(plan.seed_urls),
        domains=plan.target_domains,
        max_pages=plan.max_pages,
        js_interactions=len(plan.js_interactions),
    )

    return plan


def _summarize_input(tool_input: dict) -> str:
    """Short summary of tool input for logging."""
    if "query" in tool_input:
        return tool_input["query"][:80]
    if "url" in tool_input:
        return tool_input["url"][:100]
    return str(tool_input)[:80]
