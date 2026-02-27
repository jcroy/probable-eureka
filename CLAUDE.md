# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**webcollector** — a prompt-driven web crawling and document collection tool. Users provide a natural-language prompt describing what to collect; an agentic LLM interprets it into a structured `CrawlPlan`, then a Crawlee-based crawler executes the plan, extracts content, deduplicates, and stores results in SQLite.

## Commands

```bash
# Install all dependencies (including dev)
uv sync --all-extras

# Install Playwright browser (needed for crawling)
uv run playwright install chromium

# Run all tests
uv run pytest

# Run a single test file or test
uv run pytest tests/unit/test_database.py
uv run pytest tests/unit/test_database.py::test_insert_document

# Run with coverage
uv run pytest --cov=webcollector

# Lint
uv run ruff check .

# Type check
uv run mypy webcollector

# Run the CLI
uv run webcollector collect "your prompt here"
uv run webcollector collect --plan-file examples/sample_plan.yaml --auto-approve
uv run webcollector list-runs
uv run webcollector report <run-id>
uv run webcollector export <run-id> --format jsonl --output results.jsonl
```

## Architecture

The system is a pipeline with four stages:

```
Prompt → Interpreter → Crawler → Extractor/Dedup → Storage
```

### 1. Prompt Interpreter (`webcollector/interpreter/`)
An agentic loop using Anthropic's tool_use API (Claude Haiku). The LLM gets research tools (`web_search`, `fetch_page`, `playwright_probe`) to discover real URLs and site structure before outputting a `CrawlPlan` inside `<crawl_plan>` XML tags. Alternatively, users can skip interpretation by passing a YAML/JSON plan file via `--plan-file`.

### 2. Crawler (`webcollector/crawl/`)
`CrawlRunner` wraps Crawlee's `AdaptivePlaywrightCrawler` (HTTP-first with Playwright fallback for JS-heavy pages). It adds per-domain rate limiting (`DomainRateLimiter`) on top of Crawlee's global rate cap. `CrawlHandlers` processes each page — filtering URLs against the plan's patterns, discovering links, and triggering file downloads via `FileDownloader` (httpx streaming). JS interactions (`click`, `click_all`, `scroll_to_bottom`, etc.) are replayed on matching pages for sites with folder trees or AJAX navigation.

### 3. Extraction (`webcollector/extractor/`)
- **HTML**: `readability-lxml` extracts main content, BeautifulSoup cleans it
- **PDF**: Mistral OCR API (`mistral-ocr-latest`) is the primary extractor; `pdfplumber` is the offline fallback
- **Metadata**: Extracts title, author, published date, language from meta tags and content

### 4. Storage (`webcollector/storage/`)
- **Database**: SQLAlchemy Core (async, aiosqlite) — *not* the ORM. Tables: `crawl_runs`, `documents`, `attachments`, `sources`
- **Dedup**: SHA-256 for exact duplicates, 64-bit simhash (threshold 3 bits) for near-duplicates
- **Files**: Content-addressed filesystem layout `{sha256[:2]}/{sha256}.{ext}` under `data/raw/`

### 5. Orchestrator (`webcollector/orchestrator.py`)
`RunOrchestrator` ties the stages together. It creates a run record, launches the crawler with callbacks (`on_page_crawled`, `on_file_downloaded`), and each callback runs extraction → dedup → store.

### Key model
`CrawlPlan` (`webcollector/models/crawl_plan.py`) is the central data structure — it flows from the interpreter to the crawler and defines seed URLs, domain scope, URL patterns, depth/page limits, and JS interactions.

## Configuration

Pydantic config loaded from `webcollector.yaml` (search order: `--config` flag → CWD → `~/.webcollector/`). Config sections: `llm`, `crawl`, `browser`, `extraction`, `storage`, `logging`. API keys are read from env vars specified in config (default: `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`). The CLI auto-loads `.env` via `dotenv`.

## Code Conventions

- Python 3.11+, ruff lint rules: `E, F, I, N, W, UP, B, SIM`, line length 100
- mypy strict mode
- All async code uses `asyncio`; tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- SQLAlchemy **Core** (not ORM) — queries use `select()`, `insert()`, `update()` on `Table` objects
- `robots.txt` must always be respected — the validator on `CrawlConfig.respect_robots_txt` enforces this
- Working branch is `overhaul`; do not switch branches
