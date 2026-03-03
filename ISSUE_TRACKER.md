# Issue Tracker

Code review findings from 2026-03-03. Tracking fixes in priority order.

## Critical Issues

| # | Issue | Location | Status |
|---|-------|----------|--------|
| 1 | CancelledError swallowed | `orchestrator.py:273-275, 344` | [ ] TODO |
| 2 | PDF OOM risk (no size limit) | `pdf_extractor.py:129-130` | [ ] TODO |
| 3 | Stats race condition | `orchestrator.py:267-268, 417-430` | [ ] TODO |

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

_Updates will be logged here as fixes are committed._
