# Project Plan: Prompt-Driven Web Collection Tool

**Codename:** `webcollector`
**Version:** 0.1 (Plan)
**Date:** 2026-02-26
**Author:** Staff Engineer / Technical Product Lead

---

## A) Executive Summary

- **What:** A Python CLI/library that takes a natural-language prompt (e.g., "Collect all Boston City Council meeting minutes for 2026") and autonomously discovers, crawls, downloads, extracts, deduplicates, and stores the relevant web documents.
- **How:** An LLM interprets the user's intent into a structured crawl plan; a pipeline of source discovery, crawl orchestration, fetching, parsing, and storage executes that plan.
- **Scope:** Works across domains — government, finance, legal, academic, news, job boards, product catalogs, and more — via generic crawling plus pluggable site-specific adapters.
- **Local-first:** Runs on a developer's machine with SQLite + filesystem; optionally scales to server mode with Postgres + object storage later.
- **Reproducible:** Every run persists its inputs, config, version, and full crawl metadata so results can be audited and re-run.
- **Compliant:** Respects robots.txt, enforces rate limits, logs ToS awareness flags, and never circumvents authentication.
- **Extensible:** Plugin architecture for new site adapters, parsers, and storage backends; designed for future RAG integration (chunking, embeddings).
- **Pragmatic MVP:** First milestone delivers single-site crawl with PDF/HTML extraction in ~2-3 weeks; production-grade features layer on incrementally.
- **Tech stack:** Python 3.11+, asyncio, httpx, Playwright (JS-rendered pages), BeautifulSoup/lxml, Mistral OCR API (PDF extraction), SQLite (MVP) / Postgres (V1), structlog, Click CLI.

---

## B) Use Cases

### B.1 Government: City Council Meeting Minutes
- **Prompt:** "Collect all meeting minutes for Boston City Council in 2026, including attachments."
- **Target docs:** HTML meeting pages, PDF minutes, PDF/DOCX attachments (agendas, resolutions).
- **Hard parts:** Paginated archive pages, inconsistent file naming, attachments behind secondary link clicks, date range filtering.

### B.2 Government: Federal Register Notices
- **Prompt:** "Download all FDA proposed rules published in January 2026."
- **Target docs:** HTML rule text, PDF Federal Register notices.
- **Hard parts:** Complex search facets on federalregister.gov, multi-page results, cross-references.

### B.3 Finance: Earnings Call Transcripts
- **Prompt:** "Get all NVIDIA earnings call transcripts from 2024 and 2025."
- **Target docs:** HTML transcripts on Seeking Alpha / Motley Fool, PDF investor presentations.
- **Hard parts:** Paywalls, anti-scraping measures, JS-rendered content, login walls.

### B.4 Finance: SEC EDGAR Filings
- **Prompt:** "Collect all 10-K filings for Tesla from 2020 to 2025."
- **Target docs:** HTML filing pages, XBRL data, PDF/HTML exhibit documents.
- **Hard parts:** EDGAR's specific URL structure and index pages, large filing packages, nested exhibits.

### B.5 Legal: Court Opinions
- **Prompt:** "Find all 9th Circuit Court of Appeals opinions mentioning 'Section 230' from 2023-2025."
- **Target docs:** PDF opinions, HTML docket entries.
- **Hard parts:** PACER paywall (out of scope for free crawl), alternative free sources like CourtListener have rate limits, complex search queries.

### B.6 Legal: Regulatory Compliance Documents
- **Prompt:** "Collect GDPR guidance documents published by the European Data Protection Board."
- **Target docs:** PDF guidelines, HTML opinion pages.
- **Hard parts:** Multi-language content, deeply nested navigation, inconsistent document structure across EU sites.

### B.7 Academic: Research Papers on a Topic
- **Prompt:** "Download all open-access papers on 'transformer architectures' published in 2025 from arXiv."
- **Target docs:** PDF papers, HTML abstract pages, LaTeX source.
- **Hard parts:** Rate limiting on arXiv, bulk download policies, metadata extraction from PDF headers.

### B.8 Academic: University Course Materials
- **Prompt:** "Collect all publicly available lecture notes from MIT OpenCourseWare for 6.006."
- **Target docs:** PDF lecture notes, problem sets, HTML syllabus.
- **Hard parts:** Nested course structure, mixed media (video links vs. docs), license metadata.

### B.9 News: Topic-Based Article Collection
- **Prompt:** "Collect all Reuters and AP articles about the 2026 midterm elections from the last 30 days."
- **Target docs:** HTML articles, embedded images (optional).
- **Hard parts:** Paywalls, JS-heavy rendering, article deduplication (same story from wire syndication), rapidly changing content.

### B.10 Jobs: Job Postings for a Role
- **Prompt:** "Scrape all 'Staff Engineer' job postings from Greenhouse-hosted job boards for YC S25 companies."
- **Target docs:** HTML job descriptions, structured JSON-LD data.
- **Hard parts:** Hundreds of individual Greenhouse subdomains, JS-rendered listings, high volume, identifying which companies are YC S25.

### B.11 Product/Technical: Product Documentation
- **Prompt:** "Download the complete Stripe API documentation, all pages."
- **Target docs:** HTML doc pages, code examples, PDF if available.
- **Hard parts:** SPA navigation (JS-rendered), sidebar-driven page structure, versioned docs, large page count.

### B.12 Product/Technical: Software Changelogs
- **Prompt:** "Collect all release notes for Kubernetes from v1.28 to v1.31."
- **Target docs:** HTML release pages, Markdown changelogs on GitHub.
- **Hard parts:** GitHub API rate limits, large Markdown files, cross-linked issues/PRs.

### B.13 Real Estate: Property Listings
- **Prompt:** "Collect all commercial property listings in downtown Chicago from LoopNet."
- **Target docs:** HTML listing pages, embedded images, PDF brochures.
- **Hard parts:** Heavy JS rendering, anti-bot protections, pagination, structured data extraction from semi-structured HTML.

### B.14 Healthcare: Clinical Trial Data
- **Prompt:** "Download all Phase 3 clinical trials for GLP-1 receptor agonists from ClinicalTrials.gov."
- **Target docs:** HTML study pages, XML structured data, PDF protocols.
- **Hard parts:** Complex search filters, XML-heavy data model, large result sets, nested study documents.

---

## C) Requirements

### C.1 Functional Requirements

| # | Requirement |
|---|---|
| FR-01 | Accept a natural-language prompt describing what to collect. |
| FR-02 | Use an LLM to interpret the prompt into a structured crawl plan (target domains, URL patterns, date ranges, doc types, search queries). |
| FR-03 | Discover source URLs via search engines, sitemaps, RSS feeds, and link-graph traversal. |
| FR-04 | Crawl discovered pages respecting a configurable depth limit and URL scope. |
| FR-05 | Download documents in HTML, PDF, DOCX, XLSX, CSV, XML, and plain text formats. |
| FR-06 | Extract clean text content from all supported formats. |
| FR-07 | Extract metadata: title, date, author, source URL, content type, file size, language. |
| FR-08 | Deduplicate documents by canonical URL, content hash, and simhash fingerprint. |
| FR-09 | Store raw files, extracted text, metadata, and crawl manifests. |
| FR-10 | Support incremental crawls — detect new/changed content since last run. |
| FR-11 | Provide a CLI with commands: `collect`, `status`, `export`, `list-runs`, `rerun`. |
| FR-12 | Generate a structured run report (JSON + human-readable) after each crawl. |
| FR-13 | Support site-specific adapter plugins for known complex sites. |
| FR-14 | Support configurable output formats: JSON-lines, CSV, SQLite dump, filesystem tree. |
| FR-15 | Allow user to review and approve the crawl plan before execution. |
| FR-16 | Support resume/retry of interrupted crawls. |

### C.2 Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Performance** | Sustain 10+ pages/sec on generic sites with asyncio concurrency (configurable). |
| **Performance** | Process a 500-page crawl in under 10 minutes on commodity hardware. |
| **Reliability** | Retry transient HTTP errors (429, 5xx) with exponential backoff. |
| **Reliability** | Checkpoint crawl state every N pages for crash recovery. |
| **Security** | Never store or transmit user credentials; adapters that need auth use env vars or a credential store reference. |
| **Security** | Sanitize all file paths derived from URLs to prevent path traversal. |
| **Compliance** | Fetch and obey robots.txt before crawling any domain. |
| **Compliance** | Enforce per-domain rate limits (default: 1 req/sec, configurable). |
| **Compliance** | Log a warning when a site's ToS prohibits scraping (heuristic check). |
| **Compliance** | Set a descriptive User-Agent string with contact info. |
| **Scalability** | Single-machine MVP; architecture allows future distributed workers. |
| **Observability** | Structured JSON logging with correlation IDs per crawl run. |

---

## D) System Architecture

### D.1 High-Level Component Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                         CLI / API                            │
│  (Click CLI)       (FastAPI — future)                        │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────┐   ┌───────────────────────────────┐
│   Query Interpreter      │   │   Run Manager                 │
│   (LLM: plan generation) │   │   (state, resume, reporting)  │
└──────────┬───────────────┘   └───────────────┬───────────────┘
           │  CrawlPlan                        │
           ▼                                   ▼
┌──────────────────────────────────────────────────────────────┐
│                    Crawl Orchestrator                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Source    │  │ URL      │  │ Politeness│  │ Checkpoint   │ │
│  │ Discovery│→ │ Frontier │→ │ Engine    │→ │ Manager      │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │  URLs to fetch
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                      Fetcher Pool                            │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ HTTP Client  │  │ Browser Pool │  │ File Downloader  │    │
│  │ (httpx)      │  │ (Playwright) │  │ (streams)        │    │
│  └─────────────┘  └──────────────┘  └──────────────────┘    │
└──────────────────────────┬───────────────────────────────────┘
                           │  Raw responses
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                   Extraction Pipeline                        │
│  ┌────────────┐ ┌──────────┐ ┌─────────┐ ┌───────────────┐  │
│  │ HTML Parser│ │ PDF      │ │ DOCX    │ │ Metadata      │  │
│  │ (BS4/lxml) │ │ Extractor│ │ Extractor│ │ Extractor     │  │
│  └────────────┘ └──────────┘ └─────────┘ └───────────────┘  │
└──────────────────────────┬───────────────────────────────────┘
                           │  Documents
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                   Dedup & Storage                            │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐     │
│  │ Dedup      │  │ DB Writer    │  │ File Store       │     │
│  │ Engine     │→ │ (SQLite/PG)  │  │ (filesystem/S3)  │     │
│  └────────────┘  └──────────────┘  └──────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

### D.2 Key Design Decisions & Tradeoffs

| # | Decision | Rationale | Tradeoff |
|---|---|---|---|
| 1 | **asyncio as primary concurrency model** | Crawling is I/O-bound; asyncio handles thousands of concurrent connections with low overhead. | Harder to debug than threads; all I/O must be async-compatible. |
| 2 | **LLM for query interpretation, not for crawl execution** | LLMs are good at understanding intent and generating structured plans; bad at deterministic, high-volume operations. | Adds an API dependency and cost; mitigated by making LLM calls optional (user can supply a manual crawl plan). |
| 3 | **httpx for HTTP, Playwright only when needed** | 90%+ of pages don't need JS rendering. httpx is fast and lightweight. Playwright is slow and resource-heavy. | Need a reliable heuristic or config to decide when to use Playwright; default to httpx, fall back on failure or user config. |
| 4 | **SQLite for MVP, Postgres for V1** | SQLite is zero-config and file-portable, perfect for local-first. Postgres adds concurrency and scale. | SQLite has write-lock contention under heavy concurrency; mitigated with WAL mode and a single writer task. |
| 5 | **Filesystem for raw file storage, not blobs in DB** | Files on disk are inspectable, streamable, and cheap. DB stores metadata + paths. | Must handle path sanitization and directory structure; mitigated with content-addressed storage (hash-based paths). |
| 6 | **Plugin architecture via Python entry points** | Site adapters, parsers, and storage backends vary widely. Plugins allow extension without core changes. | Adds complexity; mitigated by providing a simple abstract base class and clear registration mechanism. |
| 7 | **Crawl plan approval step before execution** | Prevents wasted resources on misinterpreted prompts. User sees what will be crawled. | Adds friction; mitigated with `--auto-approve` flag for automation. |
| 8 | **Content-addressed file storage (SHA-256 paths)** | Natural dedup for identical files, safe filenames, easy integrity verification. | Filenames are opaque; mitigated by manifest files mapping hashes to original URLs/names. |
| 9 | **Robots.txt is mandatory, not optional** | Legal and ethical compliance is non-negotiable. | May block access to desired content; user is informed and can seek alternative sources. |
| 10 | **Structured logging from day one** | Crawl debugging requires knowing exactly what happened; structured logs enable querying and dashboards. | Slight overhead vs print statements; negligible in practice. |

---

## E) Core Data Model

### E.1 Document Schema

```python
@dataclass
class Document:
    id: str                    # UUID
    crawl_run_id: str          # FK to CrawlRun
    source_url: str            # Original URL fetched
    canonical_url: str         # Normalized URL (stripped tracking params, etc.)
    content_hash: str          # SHA-256 of raw content bytes
    simhash: str               # 64-bit SimHash of extracted text (for near-dedup)
    content_type: str          # MIME type (text/html, application/pdf, etc.)
    title: str | None
    author: str | None
    published_date: date | None
    language: str | None       # ISO 639-1 code
    extracted_text: str | None # Cleaned text content
    text_length: int
    raw_file_path: str         # Relative path in file store
    extracted_file_path: str | None  # Path to .txt extraction
    file_size_bytes: int
    fetch_status: int          # HTTP status code
    fetch_timestamp: datetime
    depth: int                 # Crawl depth from seed
    parent_url: str | None     # URL that linked to this document
    metadata_json: str | None  # Freeform JSON for domain-specific fields
    is_duplicate: bool         # True if dedup engine flagged it
    duplicate_of_id: str | None
    created_at: datetime
```

### E.2 Attachment Schema

```python
@dataclass
class Attachment:
    id: str                    # UUID
    document_id: str           # FK to parent Document
    url: str                   # Download URL
    filename: str              # Original filename from URL/headers
    content_type: str
    content_hash: str
    file_size_bytes: int
    raw_file_path: str
    extracted_text: str | None
    fetch_status: int
    created_at: datetime
```

### E.3 CrawlRun Schema

```python
@dataclass
class CrawlRun:
    id: str                    # UUID
    prompt: str                # Original user prompt
    crawl_plan_json: str       # Serialized CrawlPlan from LLM
    config_snapshot_json: str  # Full config at time of run
    tool_version: str          # webcollector version string
    status: str                # pending | running | completed | failed | cancelled
    started_at: datetime
    finished_at: datetime | None
    total_urls_discovered: int
    total_urls_fetched: int
    total_documents_stored: int
    total_duplicates_found: int
    total_errors: int
    total_bytes_downloaded: int
    resume_checkpoint: str | None  # Serialized frontier state for resume
    created_at: datetime
```

### E.4 Source Schema

```python
@dataclass
class Source:
    id: str                    # UUID
    crawl_run_id: str
    domain: str
    seed_url: str              # Entry-point URL
    discovery_method: str      # sitemap | search | link_graph | manual
    robots_txt_fetched: bool
    robots_txt_allows: bool
    rate_limit_rps: float      # Requests per second for this domain
    pages_crawled: int
    created_at: datetime
```

### E.5 Deduplication Strategy

1. **Exact URL dedup:** Normalize URLs (lowercase host, sort query params, strip fragments/tracking params like `utm_*`). Check canonical URL against DB before fetching.
2. **Content hash dedup:** SHA-256 of raw response body. After fetch, check hash against DB. If match, mark as duplicate, skip extraction.
3. **Near-duplicate detection:** Compute 64-bit SimHash of extracted text. Documents with Hamming distance ≤ 3 are flagged as near-duplicates. Store the `duplicate_of_id` pointing to the first-seen version.
4. **Cross-run dedup:** On incremental crawls, compare content hash against all previous runs. Only store + extract if content changed.

---

## F) Pipeline Design

### F.1 Query Interpretation (LLM)

**Input:** Raw user prompt string.
**Output:** `CrawlPlan` object.

```python
@dataclass
class CrawlPlan:
    intent_summary: str           # One-line description of what user wants
    target_domains: list[str]     # e.g. ["boston.gov", "bostoncouncil.org"]
    seed_urls: list[str]          # Starting points
    search_queries: list[str]     # Google/Bing queries to discover sources
    url_patterns: list[str]       # Regex/glob patterns for in-scope URLs
    exclude_patterns: list[str]   # Patterns to skip
    date_range: tuple[date, date] | None
    document_types: list[str]     # ["pdf", "html", "docx"]
    max_depth: int                # Crawl depth from seeds
    max_pages: int                # Safety cap
    keywords: list[str]           # For relevance filtering
    adapter_hint: str | None      # e.g. "sec_edgar" if a known adapter matches
```

**Implementation notes:**
- System prompt instructs the LLM to output JSON matching `CrawlPlan`.
- Use a small, fast model (e.g., Claude Haiku or GPT-4o-mini) to minimize cost and latency.
- Provide few-shot examples covering government, finance, academic, etc.
- If LLM is unavailable, user can supply a YAML crawl plan file directly (`--plan-file`).

### F.2 Source Discovery

**Methods (executed in parallel where possible):**

1. **Search engine queries:** Use SerpAPI, Brave Search API, or `googlesearch-python` to find relevant pages. Execute each `search_queries` entry.
2. **Sitemap parsing:** Fetch `/sitemap.xml` and `/sitemap_index.xml` for each target domain. Filter URLs by `url_patterns` and `date_range`.
3. **RSS/Atom feeds:** Check for feed links in HTML `<head>`. Parse feeds for recent URLs.
4. **Link-graph traversal:** From seed URLs, follow links matching `url_patterns` up to `max_depth`.
5. **Site-specific adapters:** If `adapter_hint` matches a registered adapter, use its custom discovery logic (e.g., EDGAR full-text search API).

**Output:** A deduplicated set of URLs added to the URL Frontier.

### F.3 Crawl Orchestration

**URL Frontier:**
- Priority queue (heapq or Redis for distributed).
- Priority factors: depth (lower = higher priority), discovery method (sitemap > search > link), freshness.
- Tracks state per URL: `pending`, `in_progress`, `completed`, `failed`, `skipped`.

**Politeness Engine:**
- Per-domain token bucket rate limiter.
- Default: 1 request/second/domain (configurable per domain in config).
- Reads `Crawl-delay` from robots.txt if present.
- Randomized delay jitter (±20%) to avoid detection as bot.

**Checkpoint Manager:**
- Every 50 fetched URLs (configurable), serialize frontier state + fetch log to disk.
- On resume, reload checkpoint and skip already-completed URLs.

**Orchestrator loop (pseudocode):**
```
while frontier.has_pending():
    url = frontier.next()
    if not robots_checker.is_allowed(url):
        frontier.mark_skipped(url, reason="robots.txt")
        continue
    await rate_limiter.acquire(domain_of(url))
    response = await fetcher.fetch(url)
    documents = await extractor.extract(response)
    for doc in documents:
        new_urls = link_extractor.extract_links(doc, scope=crawl_plan)
        frontier.add_many(new_urls)
    await storage.store(documents)
    frontier.mark_completed(url)
    checkpoint_manager.maybe_checkpoint()
```

### F.4 Fetching / Downloading

**Decision tree for fetch method:**

```
Is URL a direct file (PDF/DOCX/etc by extension or Content-Type)?
  → Use httpx streaming download
Does the site require JS rendering? (config flag OR previous httpx fetch returned empty/stub content)
  → Use Playwright (headless Chromium)
Otherwise:
  → Use httpx (async, connection pooling, HTTP/2)
```

**httpx configuration:**
- Connection pool: 100 connections per domain, 500 total.
- Timeout: 30s connect, 60s read, 120s for large file downloads.
- Retry: 3 attempts with exponential backoff on 429/5xx.
- Follow redirects (max 5 hops).
- Custom User-Agent: `webcollector/0.1 (+https://github.com/yourorg/webcollector; contact@yourorg.com)`

**Playwright configuration:**
- Pool of 3 browser contexts (configurable).
- Wait for `networkidle` event (max 15s).
- Block unnecessary resources: images, fonts, media (configurable).
- Extract final rendered HTML via `page.content()`.

### F.5 Parsing & Extraction

| Format | Library | Strategy |
|---|---|---|
| HTML | `lxml` + `readability-lxml` | Use readability to extract main content. Fall back to full `lxml` tree for structured data. |
| PDF | Mistral OCR API (`mistral-ocr-latest`) | Send PDF to Mistral OCR endpoint. Returns structured Markdown with layout, tables, and math preserved. Handles both text-layer and scanned/image PDFs in a single call. Far cheaper and more accurate than local Tesseract. |
| PDF (fallback) | `pdfplumber` | Offline fallback if Mistral API is unavailable or user opts out. Text-layer PDFs only. |
| DOCX | `python-docx` | Extract paragraphs, tables, headers. Preserve structure as Markdown. |
| XLSX/CSV | `openpyxl` / `csv` | Convert to structured text (pipe-delimited or Markdown table). |
| XML | `lxml.etree` | Parse and extract text nodes. Handle namespaces. |
| Plain text | — | Store as-is. |

**Link & attachment extraction:**
- From HTML: extract `<a href>` for links, identify download links by extension/MIME.
- From PDF: extract embedded URLs if present.
- Normalize all extracted URLs against the page's base URL.

### F.6 Metadata Extraction

**Rule-based (always runs):**
- Title: HTML `<title>`, PDF metadata, DOCX properties.
- Date: HTML `<meta>` tags, URL path patterns (`/2026/01/15/...`), PDF `CreationDate`.
- Author: HTML `<meta name="author">`, PDF metadata, byline regex patterns.
- Language: `langdetect` on first 1000 chars of extracted text.
- Content-Type: from HTTP `Content-Type` header.

**LLM-assisted (optional, configurable):**
- For documents where rule-based extraction yields incomplete metadata, optionally send the first 500 chars to an LLM to extract title, date, author, summary.
- Gated behind `--llm-metadata` flag to control cost.

### F.7 Storage

**Filesystem layout:**
```
data/
  runs/
    {run_id}/
      crawl_plan.json        # Serialized CrawlPlan
      config.json            # Config snapshot
      run_report.json        # Final report
      manifest.jsonl         # One line per document: id, url, hash, paths
      raw/
        {sha256_prefix2}/{sha256}.{ext}   # Raw downloaded files
      extracted/
        {sha256_prefix2}/{sha256}.txt     # Extracted text
      checkpoints/
        checkpoint_{n}.json  # Frontier snapshots
```

**Database (SQLite MVP):**
- Tables: `crawl_runs`, `sources`, `documents`, `attachments`.
- Indexes on: `canonical_url`, `content_hash`, `crawl_run_id`, `published_date`.
- WAL mode enabled for concurrent read access.

**Future:** Postgres for multi-user/server mode. S3-compatible object store for raw files. Both swap in via the storage plugin interface.

### F.8 Incremental Updates & Change Detection

1. **URL-based:** On re-run of same prompt, compare discovered URLs against previous run. Only fetch new URLs.
2. **ETag/Last-Modified:** Store HTTP response headers. On subsequent fetches, send `If-None-Match` / `If-Modified-Since`. Skip extraction if 304.
3. **Content hash:** If URL was re-fetched (no caching headers), compare SHA-256 against stored hash. Skip if unchanged.
4. **Change manifest:** Generate a diff report: new documents, changed documents, removed documents (URL no longer exists/404).

---

## G) Interfaces

### G.1 CLI Commands

```bash
# Basic collection
webcollector collect "Collect all Boston City Council meeting minutes for 2026"

# With options
webcollector collect \
  "Download all 10-K filings for Tesla 2020-2025" \
  --max-pages 500 \
  --output-dir ./tesla-filings \
  --format jsonl \
  --auto-approve

# From a plan file (skip LLM)
webcollector collect --plan-file crawl_plan.yaml

# Check status of a running crawl
webcollector status <run-id>

# List all runs
webcollector list-runs

# Export results
webcollector export <run-id> --format csv --output results.csv

# Resume an interrupted crawl
webcollector resume <run-id>

# Re-run with same plan (incremental)
webcollector rerun <run-id>

# Show run report
webcollector report <run-id>

# Validate a crawl plan without executing
webcollector plan "Collect all Reuters articles about elections" --dry-run
```

### G.2 Config File Format (YAML)

```yaml
# ~/.webcollector/config.yaml (or ./webcollector.yaml per-project)

llm:
  provider: anthropic          # anthropic | openai | ollama | none
  model: claude-haiku-4-5-20251001
  api_key_env: ANTHROPIC_API_KEY   # Env var name, never store key in config
  max_tokens: 2048
  temperature: 0.0

crawl:
  max_depth: 3
  max_pages: 1000
  default_rate_limit_rps: 1.0
  request_timeout_seconds: 30
  download_timeout_seconds: 120
  max_retries: 3
  concurrent_requests: 10
  respect_robots_txt: true      # Cannot be set to false
  user_agent: "webcollector/0.1 (+https://github.com/yourorg/webcollector)"

  # Per-domain overrides
  domain_overrides:
    sec.gov:
      rate_limit_rps: 0.5
      adapter: sec_edgar
    arxiv.org:
      rate_limit_rps: 0.33
      adapter: arxiv

fetch:
  use_playwright: auto          # auto | always | never
  playwright_pool_size: 3
  block_resources:
    - image
    - font
    - media

extraction:
  pdf_provider: mistral          # mistral | local
  mistral_api_key_env: MISTRAL_API_KEY
  mistral_model: mistral-ocr-latest
  local_ocr_enabled: false       # Only used when pdf_provider=local; requires tesseract
  local_ocr_languages: ["eng"]
  llm_metadata: false
  max_text_length: 500000        # Truncate extracted text beyond this

storage:
  backend: sqlite               # sqlite | postgres
  db_path: ./data/webcollector.db
  file_store_path: ./data
  # postgres_url: postgresql://user:pass@localhost/webcollector  # V1

output:
  default_format: jsonl         # jsonl | csv | sqlite_dump | filesystem

logging:
  level: INFO                   # DEBUG | INFO | WARNING | ERROR
  format: json                  # json | text
  file: ./logs/webcollector.log
```

### G.3 Python Package / Module Layout

```
webcollector/
├── __init__.py
├── __main__.py                  # python -m webcollector
├── cli.py                       # Click CLI definitions
├── config.py                    # Config loading, validation, defaults
├── models/
│   ├── __init__.py
│   ├── crawl_plan.py            # CrawlPlan dataclass
│   ├── document.py              # Document, Attachment dataclasses
│   ├── crawl_run.py             # CrawlRun, Source dataclasses
│   └── enums.py                 # Status enums, content types
├── interpreter/
│   ├── __init__.py
│   ├── llm_interpreter.py       # LLM-based query → CrawlPlan
│   ├── plan_file_loader.py      # YAML/JSON plan file → CrawlPlan
│   └── prompts/
│       └── interpret_query.txt  # System prompt template
├── discovery/
│   ├── __init__.py
│   ├── search.py                # Search engine queries
│   ├── sitemap.py               # Sitemap fetching + parsing
│   ├── rss.py                   # RSS/Atom feed discovery
│   └── link_graph.py            # Link extraction + scope filtering
├── orchestrator/
│   ├── __init__.py
│   ├── frontier.py              # URL priority queue
│   ├── politeness.py            # Rate limiter, robots.txt checker
│   ├── checkpoint.py            # Checkpoint save/load
│   └── runner.py                # Main crawl loop
├── fetcher/
│   ├── __init__.py
│   ├── http_client.py           # httpx-based async fetcher
│   ├── browser.py               # Playwright browser pool
│   └── downloader.py            # Streaming file downloader
├── extractor/
│   ├── __init__.py
│   ├── html_extractor.py        # BeautifulSoup/lxml + readability
│   ├── pdf_extractor.py         # Mistral OCR API (primary) + pdfplumber fallback
│   ├── docx_extractor.py        # python-docx
│   ├── spreadsheet_extractor.py # openpyxl / csv
│   ├── xml_extractor.py         # lxml.etree
│   ├── metadata.py              # Rule-based metadata extraction
│   └── llm_metadata.py          # Optional LLM-assisted metadata
├── dedup/
│   ├── __init__.py
│   ├── url_normalizer.py        # Canonical URL logic
│   ├── content_hash.py          # SHA-256 hashing
│   └── simhash.py               # SimHash near-duplicate detection
├── storage/
│   ├── __init__.py
│   ├── base.py                  # Abstract storage interface
│   ├── sqlite_store.py          # SQLite implementation
│   ├── postgres_store.py        # Postgres implementation (V1)
│   ├── file_store.py            # Filesystem raw/extracted file storage
│   └── exporters/
│       ├── __init__.py
│       ├── jsonl.py
│       ├── csv_export.py
│       └── filesystem_export.py
├── adapters/
│   ├── __init__.py
│   ├── base.py                  # Abstract adapter interface
│   ├── sec_edgar.py             # SEC EDGAR adapter
│   ├── arxiv.py                 # arXiv adapter
│   └── generic.py               # Default generic adapter
├── reporting/
│   ├── __init__.py
│   └── run_report.py            # Generate JSON + text reports
└── utils/
    ├── __init__.py
    ├── url_utils.py             # URL parsing, normalization
    ├── file_utils.py            # Safe path generation
    ├── retry.py                 # Retry/backoff utilities
    └── hashing.py               # Hashing utilities
```

### G.4 Plugin Interfaces

**Site Adapter Plugin:**
```python
class BaseSiteAdapter(ABC):
    """Override discovery + extraction for a specific site."""

    @abstractmethod
    def matches(self, domain: str) -> bool:
        """Return True if this adapter handles the given domain."""

    @abstractmethod
    async def discover(self, plan: CrawlPlan) -> list[str]:
        """Return seed URLs specific to this site."""

    @abstractmethod
    async def extract(self, response: FetchResponse) -> list[Document]:
        """Custom extraction logic for this site's pages."""

    def get_rate_limit(self) -> float:
        """Override rate limit for this site."""
        return 1.0
```

**Parser Plugin:**
```python
class BaseParser(ABC):
    """Handle extraction for a specific content type."""

    @abstractmethod
    def supported_types(self) -> list[str]:
        """Return MIME types this parser handles."""

    @abstractmethod
    async def parse(self, content: bytes, content_type: str, url: str) -> ExtractedContent:
        """Parse raw content into extracted text + metadata."""
```

**Storage Backend Plugin:**
```python
class BaseStorageBackend(ABC):
    """Persist documents and metadata."""

    @abstractmethod
    async def store_document(self, doc: Document) -> None: ...

    @abstractmethod
    async def get_document(self, doc_id: str) -> Document | None: ...

    @abstractmethod
    async def query_documents(self, run_id: str, **filters) -> list[Document]: ...

    @abstractmethod
    async def check_duplicate(self, canonical_url: str, content_hash: str) -> str | None: ...
```

**Registration:** Plugins register via Python entry points in `pyproject.toml`:
```toml
[project.entry-points."webcollector.adapters"]
sec_edgar = "webcollector.adapters.sec_edgar:SECEdgarAdapter"

[project.entry-points."webcollector.parsers"]
pdf = "webcollector.extractor.pdf_extractor:PDFParser"

[project.entry-points."webcollector.storage"]
sqlite = "webcollector.storage.sqlite_store:SQLiteStore"
```

---

## H) Technology Choices

### H.1 Core Libraries

| Library | Purpose | Why This One |
|---|---|---|
| `httpx` | Async HTTP client | HTTP/2 support, connection pooling, async-native, clean API. Superior to `aiohttp` for our use case. |
| `playwright` | JS-rendered pages | More reliable than Selenium, async-native Python bindings, auto-manages browser binaries. |
| `beautifulsoup4` + `lxml` | HTML parsing | BS4 for ease of use + tolerant parsing; lxml as the fast parser backend. |
| `readability-lxml` | Article extraction | Battle-tested port of Mozilla Readability. Extracts main content, strips boilerplate. |
| `mistralai` (Python SDK) | PDF text extraction via Mistral OCR API | Handles text-layer and scanned PDFs in one call. Returns structured Markdown. Cheap ($0.1/1K pages), fast, excellent quality — eliminates the need for local Tesseract in most cases. |
| `pdfplumber` | Local PDF fallback | Offline fallback for text-layer PDFs when Mistral API is unavailable or user prefers local-only. |
| `python-docx` | DOCX extraction | Standard, well-maintained, handles most Word documents. |
| `click` | CLI framework | Composable, well-documented, supports complex command trees. Better than argparse for our needs. |
| `structlog` | Structured logging | JSON logging with context binding. Essential for debugging crawl runs. |
| `pydantic` | Config & data validation | Validates config files and API responses. Type-safe. |
| `sqlalchemy` (Core only) | Database abstraction | Works with SQLite and Postgres. Core (not ORM) for performance. |
| `langdetect` | Language detection | Lightweight, no large model downloads. |
| `anthropic` / `openai` | LLM API clients | For query interpretation. Anthropic preferred (Claude Haiku is fast and cheap). |
| `mistralai` | Mistral API client | For PDF-to-text via Mistral OCR (`mistral-ocr-latest`). ~$0.1/1K pages. |

### H.2 Playwright vs httpx Decision Logic

```
Use httpx (default) when:
  - Page serves static HTML
  - Content is in initial HTML response
  - Site is a simple CMS, document server, or API

Use Playwright when:
  - Config explicitly sets use_playwright: always for a domain
  - httpx fetch returns <5KB body AND page is expected to have content (likely JS-rendered)
  - Site is known SPA (React/Angular/Vue) based on framework detection in initial HTML
  - Site adapter specifies browser rendering

Auto-detection heuristic:
  1. Fetch with httpx first
  2. If response body contains JS framework markers (e.g., <div id="root"></div> with minimal content,
     "window.__NEXT_DATA__" without pre-rendered content) AND extracted text < 200 chars
  3. Re-fetch with Playwright
  4. Cache the decision per domain for the rest of the crawl run
```

### H.3 Concurrency Model

**Primary: asyncio event loop (single process)**
- All I/O (HTTP requests, file writes, DB queries) via async.
- `asyncio.Semaphore` for per-domain concurrency control.
- `asyncio.Queue` for URL frontier.
- Playwright browser pool: `asyncio.Semaphore(3)` to limit concurrent browser contexts.

**Why not threads?**
- asyncio handles thousands of concurrent I/O ops with less overhead.
- No GIL contention for I/O-bound work.

**Why not multiprocessing / Celery (yet)?**
- Overkill for single-machine MVP. Adds deployment complexity.
- Architecture supports it later: frontier becomes Redis queue, fetchers become Celery workers.

**CPU-bound work (OCR, PDF parsing) runs in a thread pool:**
```python
text = await asyncio.get_event_loop().run_in_executor(
    thread_pool, pdfplumber_extract, pdf_bytes
)
```

### H.4 Storage Options

| Tier | DB | File Store | When |
|---|---|---|---|
| **MVP** | SQLite (WAL mode) | Local filesystem | Single user, local runs |
| **V1** | PostgreSQL | Local filesystem | Multi-user, server mode |
| **V2** | PostgreSQL | S3-compatible (MinIO/AWS) | Team use, large-scale |

---

## I) Milestones & Timeline

### Milestone 1: MVP (Weeks 1–3)

**Scope:** Single-prompt → crawl → extract → store for a known site. No LLM. Manual crawl plan.

**Deliverables:**
- [ ] Project scaffolding: pyproject.toml, package structure, CI skeleton
- [ ] Config loading (YAML) with pydantic validation
- [ ] CLI: `collect --plan-file`, `list-runs`, `report`
- [ ] URL frontier with priority queue
- [ ] Politeness engine: robots.txt fetching + per-domain rate limiting
- [ ] httpx async fetcher with retry logic
- [ ] HTML extractor (readability-lxml)
- [ ] PDF extractor (Mistral OCR API primary, pdfplumber local fallback)
- [ ] Link extraction + URL normalization
- [ ] Exact dedup (canonical URL + content hash)
- [ ] SQLite storage (documents table, crawl_runs table)
- [ ] Filesystem raw file storage (content-addressed)
- [ ] JSONL export
- [ ] Structured logging (structlog)
- [ ] Checkpoint/resume for interrupted crawls
- [ ] Basic run report (JSON)
- [ ] Unit tests for all extractors, dedup, URL normalization
- [ ] Integration test: crawl a local test server

**Acceptance criteria:**
- Can crawl a 100-page static site from a YAML plan file, extract text from HTML + PDF, store results, and export as JSONL.
- Respects robots.txt and rate limits.
- Can resume after simulated interruption.
- All unit tests pass.

### Milestone 2: V1 Production (Weeks 4–7)

**Scope:** Add LLM interpretation, Playwright, more extractors, adapters, and production hardening.

**Deliverables:**
- [ ] LLM query interpreter (CrawlPlan generation from natural language)
- [ ] Crawl plan approval step (interactive CLI prompt)
- [ ] Source discovery: sitemap parsing, search engine queries
- [ ] Playwright browser pool for JS-rendered pages
- [ ] Auto-detection: httpx vs Playwright
- [ ] DOCX extractor
- [ ] Scanned PDF handling already covered by Mistral OCR (verify quality on scanned corpus)
- [ ] SimHash near-duplicate detection
- [ ] Incremental crawl support (ETag / content hash comparison)
- [ ] 2 site adapters: SEC EDGAR, arXiv
- [ ] CSV export
- [ ] CLI: `collect` (with prompt), `rerun`, `status`, `export --format csv`
- [ ] Postgres storage backend (optional, config-switchable)
- [ ] Per-domain config overrides
- [ ] Run report: human-readable summary + JSON
- [ ] Integration tests against 3+ real-world sites (with recorded fixtures)
- [ ] Golden dataset tests (known-good extraction outputs)

**Acceptance criteria:**
- Can execute `webcollector collect "Download all 10-K filings for Tesla 2020-2025"` end-to-end.
- LLM generates valid CrawlPlan; user can review before execution.
- Handles JS-rendered pages without user intervention.
- Deduplication reduces stored documents by >10% on sites with syndicated content.
- Incremental re-run skips unchanged documents.

### Milestone 3: V2 Advanced (Weeks 8–12)

**Deliverables:**
- [ ] FastAPI server mode (REST API wrapping CLI functionality)
- [ ] LLM-assisted metadata extraction
- [ ] Plugin loading via entry points
- [ ] 4+ additional site adapters (Greenhouse jobs, court opinions, GitHub releases, news sites)
- [ ] RSS/Atom feed discovery
- [ ] Spreadsheet extraction (XLSX/CSV)
- [ ] S3-compatible file storage backend
- [ ] Change detection reports (new/changed/removed documents)
- [ ] Crawl scope guardrails (LLM validates fetched pages match intent)
- [ ] Metrics dashboard (crawl rate, error rate, dedupe rate, coverage)
- [ ] Load testing (1000+ page crawl benchmarks)
- [ ] RAG-ready output (chunked text with positional metadata)
- [ ] Documentation site (MkDocs)

**Acceptance criteria:**
- FastAPI server can accept collection requests and return status/results.
- Plugin system demonstrated with at least 1 third-party adapter loaded.
- 1000-page crawl completes in <10 minutes with <1% error rate.
- RAG output validated with a downstream retrieval pipeline.

---

## J) Testing & QA Plan

### J.1 Unit Tests

| Module | Key Test Cases |
|---|---|
| `url_normalizer` | Tracking param removal, fragment stripping, case normalization, IDN handling |
| `html_extractor` | Readability extraction, link extraction, encoding handling, malformed HTML |
| `pdf_extractor` | Text extraction, empty PDF handling, encrypted PDF detection |
| `simhash` | Identical docs → distance 0, near-dupes → distance ≤3, different docs → distance >3 |
| `politeness` | Rate limiter timing, robots.txt parsing, Crawl-delay respect |
| `frontier` | Priority ordering, dedup on add, serialization/deserialization |
| `config` | Default merging, env var interpolation, validation errors |
| `crawl_plan` | LLM output parsing, malformed JSON handling, field validation |

### J.2 Integration Tests

- **Local test server:** Spin up a `aiohttp` test server with known pages, PDFs, and link structure. Validate end-to-end crawl produces expected documents.
- **Recorded fixtures:** Use `pytest-recording` (VCR.py) to record real HTTP interactions, replay in CI without hitting real sites.
- **Database round-trip:** Store documents, query them, export them, validate integrity.

### J.3 Golden Datasets

Maintain a `tests/golden/` directory with:
- Input: raw HTML/PDF files from 10+ representative sites.
- Expected output: extracted text, metadata JSON.
- Test runner compares actual extraction output against golden files.
- Update golden files deliberately when extraction improves.

### J.4 Regression Tests for Site Drift

- Weekly CI job fetches a single page from each adapter's target site.
- Compares against a structural fingerprint (CSS selectors that should exist).
- Alerts if site structure has changed (adapter may need update).

### J.5 Load Tests

- Benchmark: crawl 1000 pages from local test server, measure throughput (pages/sec), memory usage, and error rate.
- Target: >10 pages/sec, <500MB peak memory, <1% error rate.

---

## K) Observability & Ops

### K.1 Structured Log Fields

Every log entry includes:
```json
{
  "timestamp": "2026-02-26T10:30:00Z",
  "level": "INFO",
  "run_id": "abc-123",
  "component": "fetcher",
  "domain": "boston.gov",
  "url": "https://boston.gov/meetings/2026",
  "event": "fetch_completed",
  "status_code": 200,
  "content_type": "text/html",
  "response_bytes": 45230,
  "duration_ms": 342,
  "depth": 2
}
```

### K.2 Metrics (per run)

| Metric | Description |
|---|---|
| `urls_discovered` | Total URLs found by discovery phase |
| `urls_fetched` | URLs successfully fetched |
| `urls_skipped_robots` | URLs blocked by robots.txt |
| `urls_skipped_scope` | URLs outside crawl scope |
| `urls_failed` | URLs that errored after all retries |
| `fetch_rate_pages_per_sec` | Rolling average throughput |
| `bytes_downloaded` | Total raw bytes |
| `documents_stored` | Unique documents persisted |
| `duplicates_exact` | Exact-hash duplicates detected |
| `duplicates_near` | SimHash near-duplicates detected |
| `extraction_errors` | Documents that failed parsing |
| `avg_fetch_latency_ms` | Mean HTTP response time |
| `playwright_renders` | Number of pages requiring browser rendering |
| `checkpoint_count` | Number of checkpoints written |

### K.3 Run Reports

Generated on crawl completion (and available via `webcollector report <run-id>`):

```
=== Crawl Run Report ===
Run ID:      abc-123
Prompt:      "Collect all Boston City Council meeting minutes for 2026"
Status:      completed
Duration:    4m 32s
Started:     2026-02-26T10:00:00Z
Finished:    2026-02-26T10:04:32Z

--- Discovery ---
Sources found:    3 domains
Seed URLs:        12
Sitemap URLs:     187
Total discovered: 243

--- Crawling ---
Fetched:          231 / 243 (95.1%)
Skipped (robots): 4
Skipped (scope):  2
Failed:           6 (2.5%)
Avg latency:      287ms

--- Extraction ---
Documents stored: 198
  HTML pages:     142
  PDF files:      49
  DOCX files:     7
Duplicates:       33 (14.3%)
Extraction errors: 3

--- Storage ---
Total size:       128.4 MB
Database:         ./data/webcollector.db
Files:            ./data/runs/abc-123/
```

---

## L) Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Site structure changes break extractors** | Extraction fails silently or returns garbage | High | Golden dataset regression tests; site-drift monitoring job; adapter versioning. |
| 2 | **Anti-bot measures block crawling** | Entire domains become inaccessible | High | Polite crawling defaults; realistic User-Agent; Playwright for JS challenges; graceful degradation with clear error reporting. |
| 3 | **LLM generates bad crawl plans** | Crawl targets wrong pages, wastes resources | Medium | Human approval step before execution; plan validation rules; dry-run mode; few-shot prompt engineering. |
| 4 | **Rate limiting causes crawl to take hours** | User frustration, timeout issues | Medium | Concurrent cross-domain crawling; per-domain parallelism tuning; progress bar with ETA. |
| 5 | **PDF extraction quality varies wildly** | Scanned/image PDFs yield no text | Medium | Mistral OCR API handles both text-layer and scanned PDFs with high accuracy; pdfplumber local fallback for text-layer PDFs if API is unavailable; quality score on extracted text; flag low-quality extractions in report. |
| 6 | **Legal/ToS violations** | Cease-and-desist, IP blocking, legal liability | Medium | Mandatory robots.txt compliance; configurable ToS-aware warnings; user acknowledges responsibility; no credential-based access. |
| 7 | **Storage grows unbounded** | Disk full, performance degradation | Medium | Configurable `max_pages` cap; content-addressed dedup; storage usage in run reports; cleanup CLI command. |
| 8 | **LLM/OCR API unavailable or rate-limited** | Cannot generate crawl plans; PDF extraction degrades | Low | Fallback to manual plan files for LLM; pdfplumber fallback for PDF extraction; cache recent plans; support local models via Ollama. |
| 9 | **Encoding issues corrupt text** | Mojibake, lost characters | Medium | Detect encoding via `charset_normalizer`; store raw bytes alongside text; log encoding mismatches. |
| 10 | **Checkpoint corruption loses crawl progress** | Must restart long crawls from scratch | Low | Write checkpoints atomically (write-then-rename); keep last 3 checkpoints; validate on load. |
| 11 | **Scope creep: crawler follows links outside target** | Fetches irrelevant pages, wastes resources | Medium | Strict URL pattern matching; domain allowlist; depth limit; LLM relevance check (V2). |
| 12 | **Concurrency bugs in async code** | Data races, deadlocks, corrupted state | Medium | Single-writer pattern for DB and frontier; thorough async integration tests; `asyncio` debug mode in development. |
| 13 | **Dependency vulnerabilities** | Security exposure | Low | Dependabot / `pip-audit` in CI; pin dependency versions; minimal dependency surface. |
| 14 | **Playwright resource leaks** | Memory growth, zombie browser processes | Medium | Browser pool with explicit lifecycle management; timeout-based kill; resource monitoring in logs. |

---

## M) Open Questions

| # | Question | Blocking? | Context |
|---|---|---|---|
| 1 | **Which search API for source discovery?** SerpAPI ($50/mo), Brave Search API (free tier), or `googlesearch-python` (fragile, no SLA). | Yes (MVP) | Affects discovery quality and cost. Recommendation: start with Brave Search API (free tier, 1 req/sec) and abstract the interface for swapping. |
| 2 | **LLM provider for query interpretation?** Anthropic Claude Haiku vs OpenAI GPT-4o-mini vs local Ollama model. | No (MVP uses plan files) | Both are viable. Recommend Anthropic Haiku for speed/cost. Support all three via config. |
| 3 | **Should we support authenticated crawling in V1?** Some high-value sources (PACER, Seeking Alpha) require login. | No | Out of scope for MVP/V1. V2 can add credential store integration. Note: never store passwords in config files. |
| 4 | **Project name?** `webcollector` is a working name. | No | Can bikeshed later. Name should be pip-installable and not conflict with existing PyPI packages. |
| 5 | **License?** MIT vs Apache 2.0 vs proprietary. | No (pre-release) | Recommend Apache 2.0 for patent protection if open-sourcing; MIT if simplicity preferred. |
| 6 | **Minimum Python version?** 3.11 (for `TaskGroup`, better asyncio) vs 3.10 (wider compatibility). | Yes (MVP) | Recommend 3.11+. Modern asyncio features reduce boilerplate significantly. 3.10 is EOL Oct 2026. |
