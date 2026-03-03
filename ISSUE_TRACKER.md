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
| 4 | Silent DB failures | `database.py:200-204, 154, 278` | [ ] TODO |
| 5 | Non-atomic file writes | `orchestrator.py:231` | [ ] TODO |
| 6 | Crawler resource leak | `crawler.py:265-268` | [ ] TODO |
| 7 | Pagination bypasses max_depth | `handlers.py:361-370` | [ ] TODO |
| 8 | Dedup false positive on empty text | `dedup.py:28-30` | [ ] TODO |
| 9 | Dedup race condition | `orchestrator.py:216-220` | [ ] TODO |

## Medium Severity Issues

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 10 | Unprotected BeautifulSoup parsing | `handlers.py:204, 227` | [ ] TODO |

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
