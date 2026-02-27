# webcollector Audit Report

**Date:** 2026-02-27
**Branch:** `overhaul`
**Tests at time of audit:** 302 passing

---

## Table of Contents

- [Critical Issues](#critical-issues)
- [High Severity Issues](#high-severity-issues)
- [Medium Severity Issues](#medium-severity-issues)
- [Low Severity Issues](#low-severity-issues)
- [Test Coverage Gaps](#test-coverage-gaps)
- [Recommended Fix Priority](#recommended-fix-priority)

---

## Critical Issues

### 1. Race Condition in DomainRateLimiter Lock Creation

**File:** `webcollector/crawl/rate_limiter.py:27`

```python
self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
```

**Problem:** `defaultdict(asyncio.Lock)` creates locks outside the event loop. Accessing `self._locks[domain]` for a new domain in multiple concurrent coroutines creates a race condition — multiple coroutines can create separate locks for the same domain before the first one is assigned.

**Impact:** "asyncio.Lock object is not created in the running event loop" errors, or multiple locks for the same domain under concurrent access.

**Fix:**
```python
self._locks: dict[str, asyncio.Lock] = {}

async def _get_lock(self, domain: str) -> asyncio.Lock:
    if domain not in self._locks:
        self._locks[domain] = asyncio.Lock()
    return self._locks[domain]
```

---

### 2. Unbounded Pagination Seed URL Expansion

**File:** `webcollector/crawl/crawler.py:143-162`

```python
for offset in range(
    pagination.param_start, pagination.param_max + 1, pagination.param_step
):
    urls.append(template.format(offset=offset))
```

**Problem:** If the LLM generates a plan with `param_max: 1000000`, this creates 1 million seed URLs at startup, all enqueued before crawling begins. No sanity check relative to `max_pages`.

**Impact:** Out-of-memory crash or extremely long startup. Memory exhaustion before a single page is crawled.

**Fix:** Cap generated URLs at `max_pages`:
```python
max_seeds = min(
    (pagination.param_max - pagination.param_start) // pagination.param_step + 1,
    plan.max_pages or 1000,
)
```

---

### 3. Invalid/Malformed URLs Crash `get_domain()`

**File:** `webcollector/utils/url_utils.py:50-52`

```python
def get_domain(url: str) -> str:
    return urlparse(url).netloc.lower()
```

**Problem:** `urlparse()` raises `ValueError` on malformed IPv6 URLs (e.g., `"http://[invalid-ipv6/page"`). This propagates uncaught through handlers, rate limiter, metadata extraction, and dedup. Similarly, `normalize_url()` at line 21 can crash on bad IPv6.

**Impact:** A single malformed URL in a page's links crashes the entire crawl run.

**Fix:** Wrap URL parsing in try/except:
```python
def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""
```

---

### 4. Silent Config Failure on Malformed YAML

**File:** `webcollector/config.py:112-134`

```python
file_data = yaml.safe_load(f)
if isinstance(file_data, dict):
    data = file_data
```

**Problem:** If the YAML file contains a list, string, or other non-dict value, `safe_load()` returns that value, the `isinstance` check fails, and the config silently falls back to defaults. The user gets no error or warning.

**Impact:** User thinks their config is loaded when it isn't. Crawls run with unexpected default settings.

**Fix:** Log a warning or raise when the file exists but doesn't parse to a dict:
```python
if file_data is not None and not isinstance(file_data, dict):
    logger.warning("config_invalid_format", path=str(path),
                    message="Expected a YAML mapping, got %s" % type(file_data).__name__)
```

---

## High Severity Issues

### 5. Callbacks Swallow All Exceptions — Silent Data Loss

**File:** `webcollector/orchestrator.py:173-275`

```python
async def _on_page_crawled(self, page_data: dict[str, Any]) -> None:
    try:
        # ~100 lines of extraction, dedup, storage logic
        ...
    except Exception:
        self._stats.errors += 1
        logger.error("page_processing_failed", url=url, exc_info=True)
```

**Problem:** Generic `except Exception` silences all failures including DB write errors, disk full, permission errors, and programming bugs (AttributeError, KeyError). Pages are marked as crawled by Crawlee but never stored. The error counter increments but the crawler continues.

**Impact:** Silent data loss. A crawl can finish with `pages_crawled=500` but `documents_stored=47` if the DB connection drops mid-crawl. Also catches `asyncio.CancelledError` and `SystemExit`, preventing graceful shutdown.

**Fix:** Re-raise non-recoverable exceptions:
```python
except (asyncio.CancelledError, SystemExit):
    raise
except Exception:
    self._stats.errors += 1
    logger.error("page_processing_failed", url=url, exc_info=True)
```

---

### 6. Callback Errors Don't Halt the Crawler

**File:** `webcollector/crawl/handlers.py:168-169`

```python
if self._on_page_crawled:
    await self._on_page_crawled(page_data)
```

**Problem:** If the callback raises an exception (e.g., DB connection lost), the page is already marked "crawled" in Crawlee's internal state. The crawler moves on to the next page. If the callback keeps failing (DB down, disk full), the crawler processes thousands of pages with zero documents stored.

**Impact:** Long crawls can complete with inflated page counters and near-zero documents. No circuit breaker to detect persistent callback failures.

**Fix:** Track consecutive callback failures and abort if threshold exceeded:
```python
if self._consecutive_callback_failures > 10:
    raise RuntimeError("Too many consecutive callback failures, aborting crawl")
```

---

### 7. No File Size Limit on PDF Extraction

**File:** `webcollector/extractor/pdf_extractor.py:129-130`

```python
file_data = file_path.read_bytes()
b64_data = base64.b64encode(file_data).decode("utf-8")
```

**Problem:** Entire file is loaded into memory with no size check. Base64 encoding expands data by ~33%. A 500MB PDF becomes ~667MB in memory. No maximum file size validation before `read_bytes()`.

**Impact:** Out-of-memory crash on large PDFs. Also, Mistral API likely has undocumented size limits that will cause silent failures.

**Fix:**
```python
file_size = file_path.stat().st_size
max_size = 100 * 1024 * 1024  # 100MB, make configurable
if file_size > max_size:
    return PDFExtractionResult(
        text="", page_count=0, method="none",
        error=f"PDF too large: {file_size} bytes (max {max_size})"
    )
```

---

### 8. Database Inserts Not Validated

**File:** `webcollector/storage/database.py:156-158`

```python
async def insert_crawl_run(self, run: dict[str, Any]) -> str:
    async with self.engine.begin() as conn:
        await conn.execute(crawl_runs.insert().values(**run))
    return run["id"]
```

**Problem:** No `rowcount` validation after insert. If the insert silently fails (constraint violation, permission issue), the function returns the ID as if it succeeded. Downstream code (orchestrator callbacks, stats updates) operates on a non-existent record.

**Impact:** Orphaned state — stats say documents were stored, but DB has no records. The same pattern exists in `insert_document()` and `insert_attachment()`.

**Fix:**
```python
result = await conn.execute(crawl_runs.insert().values(**run))
if result.rowcount != 1:
    raise RuntimeError(f"Failed to insert crawl run {run['id']}: rowcount={result.rowcount}")
```

---

### 9. Pagination Depth=0 Bypasses max_depth

**File:** `webcollector/crawl/handlers.py:369`

```python
await context.add_requests(
    [Request.from_url(next_url, user_data={"depth": 0})]
)
```

**Problem:** Paginated links always get `depth: 0`, regardless of the current page's depth. If `max_depth=2`, you can crawl 2 levels deep + unlimited pagination at each level, effectively bypassing the depth limit.

**Impact:** Crawls go much deeper than configured, potentially crawling far more pages than intended.

**Fix:** Preserve current depth:
```python
depth = context.request.user_data.get("depth", 0)
await context.add_requests(
    [Request.from_url(next_url, user_data={"depth": depth})]
)
```

---

### 10. No Graceful Shutdown / Ctrl+C Handling

**File:** `webcollector/cli.py:136`, `webcollector/orchestrator.py`

**Problem:** The CLI uses `asyncio.run()` with no signal handling. When the user presses Ctrl+C:
1. `asyncio.CancelledError` is raised
2. Falls into generic `except Exception` in orchestrator
3. Crawl marked as "failed" with no recovery
4. DB transactions may be left open
5. Raw traceback shown to user

**Impact:** Users lose all progress on long crawls. No way to resume. Especially painful for crawls with `max_pages=10000+`.

**Fix:** Add signal handler in CLI:
```python
try:
    asyncio.run(run_crawl(...))
except KeyboardInterrupt:
    click.echo("\nCrawl interrupted. Partial results saved.")
    sys.exit(130)
```

And in the orchestrator, handle `CancelledError` separately to save partial state.

---

### 11. Plan File Loader Has Unhandled Parse Errors

**File:** `webcollector/interpreter/plan_file_loader.py:13-31`

```python
if path.suffix in (".yaml", ".yml"):
    data = yaml.safe_load(text)  # Can raise YAMLError
elif path.suffix == ".json":
    data = json.loads(text)  # Can raise JSONDecodeError
```

**Problem:** `yaml.YAMLError` and `json.JSONDecodeError` are not caught. Empty files return `None` from `safe_load()`, which fails the `isinstance(data, dict)` check with an unclear error message.

**Impact:** Users loading malformed plan files get raw Python tracebacks instead of helpful error messages.

**Fix:** Catch parse errors and raise with context:
```python
try:
    data = yaml.safe_load(text)
except yaml.YAMLError as e:
    raise ValueError(f"Invalid YAML in plan file {path}: {e}") from e
```

---

### 12. Missing Browser/Crawler Cleanup on Error

**File:** `webcollector/crawl/crawler.py:250-253`

```python
try:
    await crawler.run(seed_requests)
finally:
    await self._downloader.close()
```

**Problem:** The `finally` block only closes the downloader. The Crawlee `AdaptivePlaywrightCrawler` object is never explicitly closed. If an exception occurs, the browser process may not be terminated.

**Impact:** Zombie Chromium processes remain after crash, consuming memory and file descriptors. On long-running systems, this causes resource exhaustion.

**Fix:**
```python
finally:
    await self._downloader.close()
    await crawler.close()
```

---

## Medium Severity Issues

### 13. Dedup False Positive on Empty Text

**File:** `webcollector/storage/dedup.py:29-30`

```python
if not tokens:
    return 0
```

**Problem:** Empty text returns `simhash=0`. Two completely different pages that both happen to have empty extracted text will be flagged as near-duplicates of each other.

**Impact:** Legitimate pages with empty extractions are incorrectly deduplicated. Only one is stored, the rest are marked as duplicates.

**Fix:** Return a sentinel value for empty text:
```python
if not tokens:
    return -1  # Sentinel: never matches any real simhash
```

---

### 14. Stats Counters Not Thread-Safe

**File:** `webcollector/orchestrator.py:417-430`

```python
class RunStats:
    def __init__(self) -> None:
        self.pages_crawled: int = 0
        self.documents_stored: int = 0
        self.duplicates_found: int = 0
```

Incremented from concurrent callbacks:
```python
self._stats.documents_stored += 1  # Not atomic
```

**Problem:** `+=` on an int is not atomic (read-increment-write). With `max_concurrency=10`, two callbacks can read the same value, increment it, and write back — losing one increment.

**Impact:** Final stats are inaccurate. Reports may show fewer documents than actually stored.

**Fix:** Use `threading.Lock` or `asyncio.Lock` around increments, or use atomic counters.

---

### 15. BeautifulSoup Parsing Unprotected

**File:** `webcollector/crawl/handlers.py:204, 227, 469`

```python
soup = BeautifulSoup(resp.text, "lxml")
```

**Problem:** BeautifulSoup and lxml can raise exceptions on malformed HTML, encoding issues, or memory exhaustion on huge documents. These calls are not wrapped in try/except.

**Impact:** A single malformed HTML page crashes the entire crawl run.

**Fix:**
```python
try:
    soup = BeautifulSoup(html, "lxml")
except Exception as e:
    logger.warning("soup_parse_failed", url=url, error=str(e))
    return "", None
```

---

### 16. Interpreter Loop Burns Tokens on Stuck LLM

**File:** `webcollector/interpreter/prompt_interpreter.py:228-293`

**Problem:** If the LLM keeps returning text that doesn't contain valid `<crawl_plan>` JSON, the loop:
1. Calls `_extract_crawl_plan()` → returns `None` (malformed JSON)
2. Appends "Please output your crawl plan now" message
3. Repeats up to 15 times

At `max_tokens=4096` per call, this can consume ~60K tokens before failing. No indication to the user *why* extraction failed (malformed JSON? incomplete? wrong structure?).

**Impact:** Expensive API costs for unusable results. Poor error diagnostics.

**Fix:** Log the specific parse failure reason and consider reducing max retries for plan extraction (vs. tool use turns).

---

### 17. Profile Escalation Blocks Crawler — No Timeout

**File:** `webcollector/crawl/handlers.py:466-481`

```python
if profile is None and self._escalation_manager:
    profile = await self._escalation_manager.escalate(
        signals=signals, ...
    )
```

**Problem:** Called from `default_handler()` inside Crawlee's request context. If the LLM API is slow (10+ seconds) or down, the handler blocks. With `concurrency=10`, all handlers can block simultaneously. No timeout or circuit breaker.

**Impact:** Crawl throughput drops to zero during API outages. All concurrent handlers are blocked waiting for LLM responses.

**Fix:** Add timeout:
```python
try:
    profile = await asyncio.wait_for(
        self._escalation_manager.escalate(signals=signals, ...),
        timeout=15.0,
    )
except asyncio.TimeoutError:
    logger.warning("escalation_timeout", url=url)
    profile = None
```

---

### 18. File Writes Not Atomic

**File:** `webcollector/orchestrator.py:225-231`

```python
raw_abs_path.write_bytes(html_bytes)
```

**Problem:** `Path.write_bytes()` is not atomic. If two concurrent tasks download the same content:
1. Both compute the same hash
2. Both create the same file path
3. Task A starts writing
4. Task B overwrites mid-write
5. File is corrupted

**Impact:** File corruption under concurrent load. Content-addressed dedup relies on a single-writer assumption that isn't enforced.

**Fix:** Write to a temp file, then atomic rename:
```python
import tempfile
tmp = Path(tempfile.mktemp(dir=raw_abs_path.parent))
tmp.write_bytes(html_bytes)
tmp.rename(raw_abs_path)
```

---

### 19. Regex Patterns Recompiled on Every URL Check

**File:** `webcollector/utils/url_utils.py:68`

```python
def url_matches_patterns(url: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, url) for pattern in patterns)
```

**Problem:** Each call recompiles regex patterns from strings. This is called for every URL discovered during crawling. The handler pre-compiles patterns in `__init__` (lines 91-92) but then calls this function with raw strings from `plan.exclude_patterns`.

**Impact:** Performance degradation on large crawls with many URLs. Unnecessary CPU overhead.

**Fix:** Use pre-compiled patterns from `CrawlHandlers`, or cache with `re.compile()`:
```python
_pattern_cache: dict[str, re.Pattern] = {}

def url_matches_patterns(url: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p not in _pattern_cache:
            _pattern_cache[p] = re.compile(p)
        if _pattern_cache[p].search(url):
            return True
    return False
```

---

### 20. Swapped Date Ranges Silently Accepted

**File:** `webcollector/orchestrator.py:354-370`

```python
def _is_in_date_range(self, pub_date: date | None) -> bool:
    if pub_date is None:
        return True
    if self._plan.date_range_start and pub_date < self._plan.date_range_start:
        return False
    if self._plan.date_range_end and pub_date > self._plan.date_range_end:
        return False
    return True
```

**Problem:** If the user provides `date_range_start: 2025-01-01` and `date_range_end: 2024-01-01` (backwards), no validation error is raised. The filter silently rejects all documents. User waits for a full crawl and gets zero results.

**Impact:** Wasted crawl time and user confusion.

**Fix:** Validate during plan construction or orchestrator init:
```python
if self._plan.date_range_start and self._plan.date_range_end:
    if self._plan.date_range_start > self._plan.date_range_end:
        raise ValueError(
            f"date_range_start ({self._plan.date_range_start}) is after "
            f"date_range_end ({self._plan.date_range_end})"
        )
```

---

### 21. Database Race Condition in Dedup

**File:** `webcollector/storage/database.py:200-204`, `webcollector/orchestrator.py:217`

**Problem:** The dedup check (`_dedup.check_exact()`) is in-memory and happens before the DB insert. With concurrent callbacks:
1. Task A checks hash → not found
2. Task B checks hash → not found
3. Task A inserts → succeeds
4. Task B inserts → succeeds (duplicate)

**Impact:** Exact duplicates stored despite dedup logic. Affects concurrent crawls of pages with identical content.

**Fix:** Add `ON CONFLICT IGNORE` to the insert, or use a lock around the check+insert sequence.

---

### 22. Hardcoded Timeout Values

**File:** `webcollector/crawl/handlers.py:196`, `webcollector/crawl/downloader.py:68`

```python
# handlers.py — honest UA refetch
async with httpx.AsyncClient(timeout=30, ...)

# downloader.py — connect timeout
timeout=httpx.Timeout(self._timeout, connect=30.0)
```

**Problem:** Connect timeout (30s) is hardcoded separately from the main download timeout. The honest UA refetch timeout is also hardcoded with no config option.

**Impact:** Can't tune for slow networks or specific server behavior. Inconsistent timeout strategies across modules.

**Fix:** Move all timeouts to config:
```python
timeout=self._config.crawl.connect_timeout_seconds  # New config field
```

---

### 23. DB Init Failure Causes Cascading Errors

**File:** `webcollector/orchestrator.py:65-166`

**Problem:** If `_db.init()` fails (line 72), `_db` is assigned but not initialized. The exception handler (line 143) tries `update_crawl_run()`, which calls `self._db.engine`, which raises a secondary `RuntimeError("Database not initialized")`. This masks the original error.

**Impact:** Confusing double-error traceback that obscures the root cause.

**Fix:** Guard DB operations in the error handler:
```python
except Exception:
    if self._db and self._db._engine is not None:
        await self._db.update_crawl_run(...)
```

---

### 24. Interpreter Tool Errors Swallowed Without Retry

**File:** `webcollector/interpreter/tool_executors.py:47-61, 106-107`

```python
except httpx.HTTPError as exc:
    return f"Search failed: {exc}"
```

**Problem:** Tool failures are returned as strings in the tool result. The LLM receives `"Search failed: ProxyError(...)"` but has no mechanism to retry. If multiple tools fail, the LLM may generate a plan based on zero successful research, inventing URLs.

**Impact:** Hallucinated crawl plans with non-existent domains when research tools are down.

**Fix:** Add retry logic for transient errors, and track tool success rate. If all tools fail, abort with a clear error instead of letting the LLM hallucinate.

---

## Low Severity Issues

### 25. No Disk Space Check Before Download

**File:** `webcollector/crawl/downloader.py:74-108`

**Problem:** No check for available disk space before or during download. Large files can fill the disk, and `write_bytes()` failure is not caught distinctly.

**Impact:** Disk fills up silently, subsequent writes fail.

**Fix:**
```python
import shutil
available = shutil.disk_usage(self._store_dir).free
if len(data) > available - (1 << 30):  # 1GB buffer
    logger.error("insufficient_disk_space", required=len(data), available=available)
    return None
```

---

### 26. JS Interaction Failures Are Silent

**File:** `webcollector/crawl/handlers.py:255-262`

```python
except Exception as exc:
    logger.warning(...)
    # Continues without the interaction result
```

**Problem:** If a JS interaction fails (selector not found, timeout), the code continues silently. For pages where the interaction is crucial (e.g., "click to load more"), continuing without the click means missing content.

**Impact:** Silently incomplete crawls when JS interactions fail on critical pages.

---

### 27. Profile CSS Selector Errors Swallowed

**File:** `webcollector/profiles/matcher.py:144-151`

```python
for selector in fp.css_selectors:
    try:
        if soup.select_one(selector):
            score += 1
    except Exception:
        pass  # Silent
```

**Problem:** Invalid CSS selectors are silently ignored. User-created or LLM-generated profiles with bad selectors always score 0 with no diagnostic output.

**Fix:** Log the failing selector for debugging.

---

### 28. Download Retry Has No Jitter

**File:** `webcollector/crawl/downloader.py:138, 153`

```python
await asyncio.sleep(2**attempt)
```

**Problem:** Exponential backoff (2s, 4s, 16s) with no random jitter. Multiple concurrent downloads hitting the same server retry at the exact same time.

**Impact:** Thundering herd on retries, increasing likelihood of further failures.

**Fix:**
```python
import random
await asyncio.sleep(2**attempt + random.uniform(0, 1))
```

---

### 29. `safe_filename()` Drops Extension on Long Names

**File:** `webcollector/utils/file_utils.py:27-33`

```python
def safe_filename(url: str, max_length: int = 200) -> str:
    parsed = urlparse(url)
    name = parsed.path.split("/")[-1] or "index"
    name = re.sub(r'[^\w\-.]', '_', name)
    return name[:max_length]
```

**Problem:** Truncation at `max_length` doesn't preserve the file extension. A filename like `"a" * 200 + ".pdf"` becomes `"aaa...aaa"` with no `.pdf` suffix. Also no Unicode normalization — non-ASCII characters may cause filesystem issues.

**Fix:** Truncate the stem, preserve the extension:
```python
stem, ext = os.path.splitext(name)
return stem[:max_length - len(ext)] + ext
```

---

### 30. Inconsistent Error Logging Across Modules

**Files:** Various

**Examples:**
- `profiles/escalation.py:171` — `logger.error(..., exc_info=True)`
- `crawl/handlers.py:255` — `logger.warning(..., error=str(exc))`
- `orchestrator.py:273` — `logger.error(..., exc_info=True)`

**Problem:** Some modules use `exc_info=True` (includes full traceback), others use `error=str(exc)` (message only). Inconsistency makes log parsing and alerting harder.

**Fix:** Standardize: use `exc_info=True` for ERROR level, structured `error=str(exc)` for WARNING level.

---

### 31. Hardcoded Pagination Max Pages Default

**File:** `webcollector/crawl/handlers.py:109-110`

```python
self._next_selector_max = (
    pagination.max_pages if pagination and pagination.max_pages else 1000
)
```

**Problem:** Fallback of 1000 pages is hardcoded. If user doesn't specify `pagination.max_pages`, they silently get 1000 with no way to change it via config.

**Fix:** Use value from `CrawlConfig.max_pages` as the fallback.

---

### 32. Response Status Code Assumed 200 When Missing

**File:** `webcollector/crawl/handlers.py:135-136`

```python
http_response = getattr(context, "http_response", None)
status_code = http_response.status_code if http_response else 200
```

**Problem:** Assumes status 200 if `http_response` is `None`. This could mean the page was served via Playwright (reasonable), but also could mean a connection error or partial response.

**Impact:** Incorrect status codes logged for error cases.

---

### 33. Unbounded Memory Growth in `_profiled_domains`

**File:** `webcollector/crawl/handlers.py:99-100`

```python
self._profiled_domains: dict[str, SiteProfile | None] = {}
```

**Problem:** This dictionary grows indefinitely with every unique domain encountered. For crawls spanning many subdomains, memory usage grows linearly with no eviction policy.

**Impact:** On very large crawls (10,000+ unique domains), memory consumption grows unbounded.

**Fix:** Add LRU cache with a configurable size limit.

---

### 34. Honest UA Refetch Returns 403 on Timeout

**File:** `webcollector/crawl/handlers.py:183-210`

**Problem:** If the honest UA refetch times out (network error), the exception is caught and returns `("", None, 403)`. The caller interprets this as "page blocked us" rather than "network timeout," potentially triggering incorrect escalation logic.

**Fix:** Return distinct status codes for network errors vs. actual HTTP errors:
```python
except Exception:
    return ("", None, 0)  # 0 = network error, distinct from 403
```

---

### 35. No Streaming Response Cancellation Handling

**File:** `webcollector/crawl/downloader.py:86-95`

```python
async with client.stream("GET", url) as response:
    response.raise_for_status()
    async for chunk in response.aiter_bytes(chunk_size=8192):
        chunks.append(chunk)
```

**Problem:** If the task is cancelled mid-stream, chunks may be partially collected. The partial data is then joined and written to disk as if complete. No atomic write mechanism.

**Impact:** Incomplete/corrupted files stored and marked as complete.

---

## Test Coverage Gaps

### Missing Test Files

| Missing File | What It Should Cover | Priority |
|-------------|---------------------|----------|
| `tests/unit/test_cli.py` | All CLI commands, invalid args, missing files, Ctrl+C, error messages | **HIGH** |
| `tests/unit/test_config.py` | Malformed YAML, non-dict YAML, missing fields, env var resolution, override merging | **HIGH** |
| `tests/unit/test_plan_file_loader.py` | Empty files, malformed JSON/YAML, missing required fields, invalid enum values | **MEDIUM** |

### Insufficient Edge Case Coverage

| Existing File | Missing Tests | Priority |
|--------------|--------------|----------|
| `tests/unit/test_url_utils.py` | Empty strings, IPv6 addresses, data/blob/file URLs, protocol-relative URLs, `get_domain("")` | **MEDIUM** |
| `tests/unit/test_file_utils.py` | Unicode filenames, empty hash, very long names, null bytes, extension preservation on truncation | **MEDIUM** |
| `tests/unit/test_database.py` | Constraint violations, duplicate keys, NULL foreign keys, invalid field values, concurrent inserts | **MEDIUM** |
| `tests/unit/test_pipeline.py` | Callback with empty HTML, extraction failure mid-callback, date filtering edge cases, concurrent callback races | **MEDIUM** |
| `tests/unit/test_js_interactions.py` | Invalid regex in `url_pattern`, invalid CSS selectors, Playwright timeouts, handler failure recovery | **LOW** |

### Untested Error Paths

- Config loading from malformed YAML files
- Plan file loading with invalid JSON/YAML
- `KeyboardInterrupt` during `asyncio.run()` in CLI
- Database `init()` failure and cascading error handling
- PDF extractor with encrypted/corrupted PDFs
- HTML extractor with extremely large documents (>100MB)
- Dedup with empty text (simhash=0 collision)
- Profile matcher with invalid CSS selectors
- Escalation manager with LLM timeout/failure
- File downloader with disk full scenarios

---

## Recommended Fix Priority

### Phase 1 — Crash Prevention

Issues: **1, 2, 3, 4, 10, 11, 12, 15**

These can crash the crawler or silently misconfigure it. Quick defensive fixes with high impact. Estimated scope: small, targeted changes.

### Phase 2 — Data Integrity

Issues: **5, 6, 7, 8, 9, 13, 14, 18, 20, 21**

These cause silent data loss or incorrect results. Core reliability improvements. Estimated scope: moderate changes to orchestrator, database, and handlers.

### Phase 3 — Robustness & Performance

Issues: **16, 17, 19, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35**

Performance optimizations, better error handling, timeouts, and polish. Makes crawls more reliable under adverse conditions.

### Phase 4 — Test Coverage

Fill the gaps identified above: CLI tests, config tests, plan loader tests, and edge-case tests for URL utils, file utils, database, and orchestrator callbacks.
