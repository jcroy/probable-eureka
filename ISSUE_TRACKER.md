# Issue Tracker

Code review findings from 2026-03-03. Tracking fixes in priority order.

## Critical Issues

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 1 | CancelledError swallowed | `orchestrator.py:273-275, 344` | [x] FIXED |
| 2 | PDF OOM risk (no size limit) | `pdf_extractor.py:129-130` | [x] FIXED |
| 3 | Stats race condition | `orchestrator.py:267-268, 417-430` | [x] FIXED |

## High Severity Issues

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 4 | Silent DB failures | `database.py:200-204, 154, 278` | [x] FIXED |
| 5 | Non-atomic file writes | `orchestrator.py:231` | [x] FIXED |
| 6 | Crawler resource leak | `crawler.py:265-268` | [x] FIXED |
| 7 | Pagination bypasses max_depth | `handlers.py:361-370` | [x] FIXED |
| 8 | Dedup false positive on empty text | `dedup.py:28-30` | [x] FIXED |
| 9 | Dedup race condition | `orchestrator.py:216-220` | [x] FIXED |

## Medium Severity Issues

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 10 | Unprotected BeautifulSoup parsing | `handlers.py:204, 227` | [x] FIXED |

## Feature Gaps (found during testing)

| # | Issue | Impact | Status |
|---|-------|--------|--------|
| 11 | Anti-bot bypass needed | Sites like investing.com, yahoo finance block crawlers | [ ] TODO |
| 12 | Link-list pages get dropped | HN-style pages flagged as low_content_quality | [ ] TODO |
| 13 | No Cloudflare challenge handling | Many finance sites use CF protection | [ ] TODO |

---

## Change Log

### 2026-03-03 - Critical fixes

**Issue #1: CancelledError swallowed**
- Added `except (asyncio.CancelledError, SystemExit): raise` before generic exception handlers
- Affects `_on_page_crawled` and `_on_file_downloaded` callbacks
- Enables graceful shutdown on Ctrl+C

**Issue #2: PDF OOM risk**
- Added file size check in `_extract_mistral_file()` before loading PDF into memory
- Falls back to local pdfplumber extraction for files > `max_pdf_size_bytes` (default 100MB)
- Added `max_pdf_size_bytes` config option to `ExtractionConfig`

**Issue #3: Stats race condition**
- Added `_stats_lock` (asyncio.Lock) to RunOrchestrator
- Wrapped all stats counter increments with `async with self._stats_lock:`
- Prevents lost increments under concurrent callback execution

**Issue #4: Silent DB failures**
- Added rowcount validation to all insert methods in database.py
- Raises RuntimeError if insert fails silently

**Issue #5: Non-atomic file writes**
- Use temp file + atomic rename for content-addressed file storage
- Skip write if file already exists (same hash = same content)

**Issue #6: Crawler resource leak**
- Added `await crawler.stop()` in finally block to clean up browser processes

**Issue #7: Pagination bypasses max_depth**
- Preserve current depth when enqueueing pagination links
- Prevents unlimited pagination from bypassing max_depth limits

**Issue #8: Dedup false positive on empty text**
- Return EMPTY_TEXT_SIMHASH sentinel (-1) for empty text
- `is_near_duplicate()` returns False if either hash is the sentinel
- Prevents unrelated empty pages from being flagged as duplicates

**Issue #9: Dedup race condition**
- Added `_dedup_lock` to protect check-record sequence
- Lock held during exact/near check AND hash recording
- Released before DB insert to minimize lock contention

**Issue #10: Unprotected BeautifulSoup parsing**
- Wrapped BeautifulSoup parsing in try-except blocks
- Returns empty/None on parse failure instead of crashing
