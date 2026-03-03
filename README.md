# webcollector

> **Status: Proof of Concept** — Core functionality works across many site types. See [Site Compatibility](#site-compatibility) for tested sites.

**Prompt-driven web crawling and document collection tool.**

Describe what you want to collect in plain English. An agentic LLM researches the target site, builds a structured crawl plan, and a Crawlee-based crawler executes it — extracting content, deduplicating, and storing results in SQLite.

```bash
webcollector collect "get the top articles from dev.to about Python"
```

No YAML. No selectors. No config files. Just a prompt.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Site Compatibility](#site-compatibility)
- [Installation](#installation)
- [Usage](#usage)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Crawl Plans](#crawl-plans)
- [Pagination](#pagination)
- [JS-Heavy Sites](#js-heavy-sites)
- [Storage & Deduplication](#storage--deduplication)
- [Known Limitations](#known-limitations)
- [Development](#development)
- [License](#license)

---

## How It Works

```
Prompt ──> Interpreter ──> Crawler ──> Extractor ──> Storage
              │                │            │            │
         Claude Haiku     Crawlee +     readability   SQLite +
         agentic loop     Playwright      + Mistral   content-addressed
         with research     adaptive        OCR        filesystem
         tools (search,
         fetch, probe)
```

1. **Interpret** — Claude Haiku receives the user's prompt and an arsenal of research tools (`web_search`, `fetch_page`, `playwright_probe`). It searches the web, fetches candidate pages, probes JS-heavy sites with a real browser, discovers pagination patterns, and outputs a structured `CrawlPlan`.

2. **Crawl** — Crawlee's `AdaptivePlaywrightCrawler` executes the plan. HTTP-first for speed, automatic Playwright fallback for JS-rendered content. Per-domain rate limiting, automatic pagination following, and a 403-fallback mechanism that retries with an honest user-agent for sites that block headless browsers.

3. **Extract** — HTML content is cleaned via `readability-lxml`. PDFs are processed through Mistral's OCR API (`mistral-ocr-latest`) with `pdfplumber` as an offline fallback. Metadata (title, author, date, language) is parsed from meta tags and content.

4. **Deduplicate & Store** — SHA-256 for exact duplicates, 64-bit simhash (threshold: 3 bits) for near-duplicates. Documents are stored in SQLite with raw files in a content-addressed filesystem (`{sha256[:2]}/{sha256}.{ext}`).

---

## Quick Start

```bash
# Install
uv sync --all-extras
uv run playwright install chromium

# Set API keys
export ANTHROPIC_API_KEY=sk-ant-...
export MISTRAL_API_KEY=...          # optional, for PDF OCR

# Collect
uv run webcollector collect "python asyncio documentation from docs.python.org"

# Check results
uv run webcollector list-runs
uv run webcollector report <run-id>
uv run webcollector export <run-id> --format jsonl --output results.jsonl
```

---

## Site Compatibility

Tested against a variety of site types (March 2026):

### Works Well

| Site | Type | Documents | Avg Content |
|------|------|-----------|-------------|
| Dev.to | Tech blog | 105 | 9,119 chars |
| Wikipedia | Encyclopedia | 5 | 12,518 chars |
| Python Docs | Documentation | 19 | 10,907 chars |
| GitHub | Code repos | 22 | 1,286 chars |
| Project Gutenberg | Book archive | 41 | 9,401 chars |
| Old Reddit | Forum | 43 | 1,398 chars |
| Lobste.rs | Link aggregator | 44 | 1,037 chars |
| SEC EDGAR | Government filings | 55 | 20,597 chars |

### Partial Support

| Site | Issue | Notes |
|------|-------|-------|
| Stack Overflow | JS rendering | Only 2 docs extracted from 66 pages |
| Medium | Paywall + JS | 5 docs from 351 pages |
| Hacker News | Link-list detection | Flagged as low content (expected) |

### Blocked (Known Limitations)

| Site | Blocker |
|------|---------|
| Twitter/X | Login wall required |
| LinkedIn | Auth + anti-bot |
| Yahoo Finance | 503 bot detection |
| Investing.com | Cloudflare protection |
| Reuters | 401 session block |
| Bloomberg | Heavy paywall |

---

## Installation

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Chromium (installed via Playwright)

### With uv

```bash
git clone https://github.com/yourorg/webcollector.git
cd webcollector
uv sync --all-extras
uv run playwright install chromium
```

### With pip

```bash
git clone https://github.com/yourorg/webcollector.git
cd webcollector
pip install -e ".[dev]"
playwright install chromium
```

### API Keys

webcollector reads API keys from environment variables. Create a `.env` file in the project root:

```bash
ANTHROPIC_API_KEY=sk-ant-...       # Required — powers the prompt interpreter
MISTRAL_API_KEY=...                # Optional — enables Mistral OCR for PDFs
```

The CLI auto-loads `.env` via `python-dotenv`. Key variable names are configurable in `webcollector.yaml`.

### DevContainer

A `.devcontainer/` configuration is included for VS Code / GitHub Codespaces with Playwright system dependencies and the Python toolchain pre-installed.

---

## Usage

### CLI Commands

#### `collect` — Run a crawl from a prompt or plan file

```bash
# From a natural language prompt
webcollector collect "recent machine learning papers from arxiv about transformers"

# From a pre-built plan file (skips LLM interpretation)
webcollector collect --plan-file examples/sample_plan.yaml

# With overrides
webcollector collect "SEC EDGAR 10-K filings" \
    --max-pages 50 \
    --auto-approve \
    --format jsonl \
    --output edgar_results.jsonl
```

| Flag | Description |
|------|-------------|
| `--plan-file PATH` | Load a YAML/JSON crawl plan directly (skips LLM) |
| `--max-pages INT` | Hard cap on pages crawled (overrides plan and config) |
| `--auto-approve` | Skip the interactive plan confirmation prompt |
| `--format {jsonl,csv}` | Output format for results |
| `--output PATH` | Write results to file instead of stdout |

#### `list-runs` — List previous crawl runs

```bash
webcollector list-runs
```

#### `report` — Show detailed run statistics

```bash
webcollector report <run-id>
```

Displays: status, prompt, pages fetched, documents stored, duplicates found, errors, bytes downloaded.

#### `export` — Export documents from a run

```bash
webcollector export <run-id> --format jsonl --output results.jsonl
webcollector export <run-id> --format csv --output results.csv
```

#### Global Options

```bash
webcollector --config /path/to/webcollector.yaml --log-level DEBUG collect "..."
```

---

## Architecture

### Module Layout

```
webcollector/
├── cli.py                          # Click CLI (collect, list-runs, report, export)
├── config.py                       # Pydantic config with YAML loading
├── orchestrator.py                 # RunOrchestrator — ties the pipeline together
├── crawl/
│   ├── crawler.py                  # CrawlRunner — Crawlee setup and execution
│   ├── handlers.py                 # CrawlHandlers — per-page request handling
│   ├── downloader.py               # FileDownloader — streaming binary downloads
│   └── rate_limiter.py             # DomainRateLimiter — per-domain rate control
├── extractor/
│   ├── html_extractor.py           # readability-lxml + BeautifulSoup cleanup
│   ├── pdf_extractor.py            # Mistral OCR API + pdfplumber fallback
│   └── metadata.py                 # Title, author, date, language extraction
├── interpreter/
│   ├── prompt_interpreter.py       # Agentic LLM loop (Claude Haiku)
│   └── tool_executors.py           # web_search, fetch_page, playwright_probe
├── models/
│   ├── crawl_plan.py               # CrawlPlan, PaginationRule, JsInteraction
│   ├── document.py                 # Document, Attachment models
│   ├── crawl_run.py                # CrawlRun model
│   └── enums.py                    # Status enums
├── storage/
│   ├── database.py                 # SQLAlchemy Core (async, aiosqlite)
│   └── dedup.py                    # SHA-256 exact + simhash near-duplicate
└── utils/
    ├── url_utils.py                # URL normalization, matching, resolution
    ├── file_utils.py               # Content-addressed file storage
    └── hashing.py                  # SHA-256 and simhash utilities
```

### Interpreter Agent

The prompt interpreter is an agentic loop powered by Claude Haiku. It receives the user's natural language prompt and three research tools:

| Tool | What it does | When the LLM uses it |
|------|-------------|---------------------|
| `web_search` | DuckDuckGo search, returns titles + URLs + snippets | Discover domains, find entry points |
| `fetch_page` | HTTP GET (no JS), returns text + links | Inspect page structure, find URL patterns |
| `playwright_probe` | Real Chromium browser with JS interactions, returns rendered text + links + interactive elements with CSS selectors | JS-heavy sites, folder trees, AJAX navigation |

The LLM iterates (up to 15 turns by default), researching the target site until it has enough information to emit a `CrawlPlan` inside `<crawl_plan>` XML tags. It discovers pagination patterns, identifies JS interactions needed, and scopes the crawl to relevant domains and URL patterns.

### Crawler

`CrawlRunner` wraps Crawlee's `AdaptivePlaywrightCrawler`:

- **Adaptive mode** (default): Fetches via httpx first. If the result checker finds insufficient content (< 200 chars), Crawlee automatically re-fetches with Playwright.
- **HTTP-only mode**: `BeautifulSoupCrawler` — no browser, fastest option for static sites.
- **Playwright-only mode**: Every page rendered in Chromium.

On top of Crawlee's global rate cap (`max_tasks_per_minute`), `DomainRateLimiter` enforces per-domain request rates with configurable jitter to avoid detection patterns.

**403 Handling**: Sites like SEC.gov block browser-spoofed user-agents from non-browser clients. When Crawlee gets a 403, the handler retries with a plain httpx request using an honest `webcollector/1.0 (research tool)` user-agent. This three-tier fallback (httpx with stealth headers → Playwright → honest UA) handles the widest range of sites without configuration.

### Extraction

| Content Type | Primary Extractor | Fallback |
|-------------|------------------|----------|
| HTML | `readability-lxml` (article extraction) → BeautifulSoup (text cleanup) | Raw BeautifulSoup (strips script/style/nav) |
| PDF | Mistral OCR API (`mistral-ocr-latest`) | `pdfplumber` (local, no API) |
| DOCX | `python-docx` | — |

### Database Schema

Four tables managed via SQLAlchemy Core (async, aiosqlite):

- **`crawl_runs`** — Run metadata: prompt, plan JSON, config snapshot, status, aggregate stats
- **`documents`** — One row per crawled page: URL, content hash, simhash, extracted text, metadata, file paths, dedup flags
- **`attachments`** — Downloaded files (PDFs, DOCX) linked to parent documents
- **`sources`** — Reserved for future cross-referencing

---

## Configuration

webcollector loads config from YAML (search order: `--config` flag → `./webcollector.yaml` → `~/.webcollector/webcollector.yaml`).

All values have sensible defaults. A minimal config:

```yaml
llm:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  api_key_env: ANTHROPIC_API_KEY

crawl:
  max_depth: 3
  max_pages: 1000
  default_rate_limit_rps: 1.0

browser:
  rendering_mode: adaptive   # adaptive | http_only | playwright_only

extraction:
  pdf_provider: mistral
  mistral_api_key_env: MISTRAL_API_KEY

storage:
  db_path: ./data/webcollector.db
  file_store_path: ./data
```

<details>
<summary><strong>Full configuration reference</strong></summary>

```yaml
llm:
  provider: anthropic                    # LLM provider
  model: claude-haiku-4-5-20251001       # Model ID
  api_key_env: ANTHROPIC_API_KEY         # Env var name for API key
  max_tokens: 4096                       # Max tokens per LLM call
  temperature: 0.0                       # Sampling temperature
  max_research_turns: 15                 # Max agentic loop iterations

crawl:
  max_depth: 3                           # Max link depth from seeds
  max_pages: 1000                        # Max pages per run
  max_tasks_per_minute: 60               # Crawlee global rate cap
  default_rate_limit_rps: 1.0            # Default requests/sec per domain
  download_timeout_seconds: 120          # File download timeout
  max_retries: 3                         # Per-URL retry count
  max_concurrency: 10                    # Max parallel requests
  min_concurrency: 1                     # Min parallel requests
  respect_robots_txt: true               # Set to false to ignore robots.txt
  user_agent: "webcollector/0.1 (+https://github.com/yourorg/webcollector)"
  domain_overrides:                      # Per-domain settings
    example.com:
      rate_limit_rps: 0.5               # Slower rate for this domain
      rendering_mode: playwright_only    # Force browser for this domain

browser:
  playwright_pool_size: 3               # Browser instance pool size
  rendering_mode: adaptive              # adaptive | http_only | playwright_only
  block_resources: [image, font, media] # Resources to skip in browser

extraction:
  pdf_provider: mistral                 # mistral | local
  mistral_api_key_env: MISTRAL_API_KEY  # Env var for Mistral API key
  mistral_model: mistral-ocr-latest     # Mistral OCR model
  local_ocr_enabled: false              # Local OCR fallback
  local_ocr_languages: [eng]            # OCR languages
  max_text_length: 500000               # Max chars stored per document

storage:
  backend: sqlite                       # sqlite (postgres planned)
  db_path: ./data/webcollector.db       # SQLite database path
  file_store_path: ./data               # Root for content-addressed files

output:
  default_format: jsonl                 # jsonl | csv

logging:
  level: INFO                           # DEBUG | INFO | WARNING | ERROR
  format: json                          # json | text
  file: ./logs/webcollector.log         # Log file path (null to disable)
```

</details>

---

## Crawl Plans

A `CrawlPlan` is the structured specification that drives every crawl. The LLM interpreter generates one from your prompt, or you can write one by hand.

### Example: Manual Plan

```yaml
# examples/sample_plan.yaml
intent_summary: "Collect Python documentation pages for the asyncio module"
target_domains:
  - docs.python.org
seed_urls:
  - https://docs.python.org/3/library/asyncio.html
url_patterns:
  - "docs\\.python\\.org/3/library/asyncio"
exclude_patterns:
  - "\\?highlight="
document_types:
  - html
max_depth: 2
max_pages: 50
keywords:
  - asyncio
  - coroutine
  - event loop
```

```bash
webcollector collect --plan-file examples/sample_plan.yaml --auto-approve
```

### CrawlPlan Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `intent_summary` | `str` | `""` | Human-readable description of the crawl goal |
| `target_domains` | `list[str]` | `[]` | Restrict crawling to these domains (and subdomains) |
| `seed_urls` | `list[str]` | `[]` | Entry point URLs |
| `search_queries` | `list[str]` | `[]` | Search terms for URL discovery |
| `url_patterns` | `list[str]` | `[]` | Regex include patterns — URL must match at least one |
| `exclude_patterns` | `list[str]` | `[]` | Regex exclude patterns — matching URLs are skipped |
| `date_range_start` | `date \| None` | `None` | Filter by publication date (lower bound) |
| `date_range_end` | `date \| None` | `None` | Filter by publication date (upper bound) |
| `document_types` | `list[str]` | `["html", "pdf"]` | Content types to extract |
| `max_depth` | `int` | `3` | Maximum crawl depth from seed URLs |
| `max_pages` | `int` | `1000` | Maximum pages to crawl |
| `keywords` | `list[str]` | `[]` | Relevance keywords |
| `js_interactions` | `list[JsInteraction]` | `[]` | JavaScript actions for dynamic pages |
| `pagination` | `PaginationRule \| None` | `None` | Pagination strategy |

---

## Pagination

webcollector supports three pagination strategies, applied in order:

### 1. URL Parameter Expansion

For sites with offset/page-based URL parameters (e.g., `?start=0`, `?page=1`). The interpreter detects these and generates a `PaginationRule`:

```yaml
pagination:
  strategy: url_parameter
  url_template: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-K&start={offset}&count=100"
  param_start: 0
  param_step: 100
  param_max: 2000
```

The crawler expands this into seed URLs at startup (`start=0`, `start=100`, ..., `start=2000`) and enqueues them all.

### 2. Next-Selector Following

For sites with "Next" pagination links:

```yaml
pagination:
  strategy: next_selector
  next_selector: "a.pagination-next"
  max_pages: 50
```

The handler finds the element matching the CSS selector on each page and enqueues its `href`.

### 3. Auto-Detection (No Configuration Required)

When no explicit pagination rule exists, the handler automatically detects:

- `<link rel="next">` and `<a rel="next">` (web standard)
- `a[aria-label="Next"]`
- `.pagination .next a`, `.pagination a.next`, `a.pagination-next`, `li.next a`

This worked out of the box on Hacker News without any plan configuration.

---

## JS-Heavy Sites

For sites that require JavaScript execution (folder trees, AJAX-loaded content, expand/collapse widgets), the interpreter's `playwright_probe` tool detects interactive elements and generates `js_interactions`:

```yaml
js_interactions:
  - url_pattern: "clerkbase\\.com/meetings"
    steps:
      - action: click_all
        selector: "a[data-node-id][aria-expanded='false']"
        timeout_ms: 3000
      - action: wait_for_timeout
        timeout_ms: 2000
      - action: scroll_to_bottom
```

### Supported Actions

| Action | Description |
|--------|-------------|
| `click` | Click a single element matching the selector |
| `click_all` | Click all elements matching the selector |
| `wait_for_selector` | Wait for an element to appear in the DOM |
| `wait_for_timeout` | Pause for a fixed duration (ms) |
| `scroll_to_bottom` | Scroll to the bottom of the page (triggers lazy loading) |

---

## Storage & Deduplication

### File Storage

Raw files are stored in a content-addressed layout:

```
data/
├── webcollector.db
└── raw/
    ├── 4a/
    │   └── 4a7f8c9d...6f7.html
    ├── b2/
    │   └── b2e3f4a5...8c9.pdf
    └── ...
```

The first two characters of the SHA-256 hash form a sharding directory, keeping directory sizes manageable at scale.

### Deduplication

**Exact**: SHA-256 of normalized text (lowercased, whitespace-collapsed). If the hash already exists in the current run, the document is flagged as a duplicate.

**Near-duplicate**: 64-bit simhash using word-level 3-gram shingles with frequency weighting. Two documents with a Hamming distance of 3 bits or fewer (out of 64) are considered near-duplicates — roughly 95% structural similarity.

---

## Known Limitations

### Anti-Bot Protection

Sites using Cloudflare, Akamai, or similar bot detection will block or timeout. Examples: Investing.com, Yahoo Finance.

**Workaround**: None currently. These require browser fingerprint spoofing or CAPTCHA solving services.

### Authentication Walls

Sites requiring login (Twitter/X, LinkedIn, Instagram) cannot be crawled without credentials.

**Workaround**: None currently. Future versions may support cookie injection or OAuth flows.

### Heavy JavaScript Sites

Some JS-heavy sites (Stack Overflow question lists, Medium) render content that Playwright captures but extraction yields limited results due to dynamic DOM timing.

**Workaround**: Use `playwright_only` rendering mode and add explicit `wait_for_selector` JS interactions in your crawl plan.

### Link-List Pages

Pages that are primarily lists of links (Hacker News front page, Reddit listings) are flagged as "low content quality" because extracted text is minimal.

**Note**: This is expected behavior — the crawler is optimized for content pages, not navigation pages.

---

## Development

### Running Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov=webcollector

# Single file
uv run pytest tests/unit/test_database.py

# Verbose
uv run pytest -v
```

Unit tests cover: URL utilities, crawl plan validation, pagination handling, JS interactions, HTML/PDF extraction, metadata parsing, deduplication, database operations, and pipeline orchestration.

For site compatibility testing, see [SITE_TEST_MATRIX.md](SITE_TEST_MATRIX.md).

### Linting & Type Checking

```bash
# Lint (ruff)
uv run ruff check .

# Type check (mypy, strict mode)
uv run mypy webcollector
```

Ruff rules: `E, F, I, N, W, UP, B, SIM` with a 100-character line limit.

### Code Conventions

- **Async everywhere**: All I/O code uses `asyncio`. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.
- **SQLAlchemy Core**: Not the ORM. Queries use `select()`, `insert()`, `update()` on `Table` objects with `aiosqlite`.
- **Pydantic v2**: All models and configuration use Pydantic v2 with strict validation.
- **structlog**: Structured logging throughout, JSON or text format configurable.
- **Type-safe**: `mypy --strict` enforced.

### Dependencies

| Category | Package | Purpose |
|----------|---------|---------|
| Crawl engine | `crawlee[beautifulsoup,parsel,playwright]` | Adaptive HTTP + browser crawling |
| HTTP | `httpx` | File downloads, interpreter fetches |
| HTML extraction | `readability-lxml`, `beautifulsoup4`, `lxml` | Content extraction and cleanup |
| PDF extraction | `mistralai`, `pdfplumber` | OCR API + local fallback |
| LLM | `anthropic` | Claude Haiku for prompt interpretation |
| Search | `duckduckgo-search` | Web search in interpreter agent |
| Database | `sqlalchemy`, `aiosqlite` | Async SQLite storage |
| Config | `pydantic`, `pyyaml` | Typed config with YAML loading |
| CLI | `click` | Command-line interface |
| Logging | `structlog` | Structured logging |
| NLP | `langdetect` | Language detection |

---

## License

Apache-2.0
