# Site Test Matrix

Comprehensive list of sites to test webcollector against, organized by category and expected difficulty.

## Test Categories

### 1. Easy (Static HTML, crawler-friendly)
| Site | URL | Type | Expected |
|------|-----|------|----------|
| Hacker News | https://news.ycombinator.com/ | Link list | Works, low content |
| Wikipedia | https://en.wikipedia.org/wiki/S%26P_500 | Article | Should work |
| SEC EDGAR | https://www.sec.gov/cgi-bin/browse-edgar | Filings | Should work |
| Project Gutenberg | https://www.gutenberg.org/ | Books | Should work |
| Archive.org | https://archive.org/ | Archive | Should work |
| Lobste.rs | https://lobste.rs/ | Link list | Should work |
| Read the Docs | https://docs.python.org/ | Documentation | Should work |

### 2. Medium (JS required, but no hard blocking)
| Site | URL | Type | Expected |
|------|-----|------|----------|
| GitHub README | https://github.com/anthropics/claude-code | Repo page | Needs Playwright |
| Dev.to | https://dev.to/ | Blog | Needs Playwright |
| Medium (public) | https://medium.com/tag/technology | Articles | Partial paywall |
| Stack Overflow | https://stackoverflow.com/questions | Q&A | Should work |
| Reddit (old) | https://old.reddit.com/r/stocks | Forum | Should work |

### 3. Hard (Anti-bot, auth walls)
| Site | URL | Type | Blocker |
|------|-----|------|---------|
| Twitter/X | https://x.com/search | Social | Auth required |
| LinkedIn | https://linkedin.com/ | Social | Auth + anti-bot |
| Yahoo Finance | https://finance.yahoo.com/ | Finance | 503 bot block |
| Investing.com | https://investing.com/ | Finance | Cloudflare |
| Reuters | https://reuters.com/ | News | 401 session block |
| Bloomberg | https://bloomberg.com/ | Finance | Heavy paywall |

### 4. Document-heavy (PDFs, downloads)
| Site | URL | Type | Expected |
|------|-----|------|----------|
| SEC EDGAR filings | https://www.sec.gov/cgi-bin/browse-edgar | PDFs | Should work |
| FDA letters | https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations | PDFs | Should work |
| Court opinions | https://www.supremecourt.gov/opinions | PDFs | Should work |
| arXiv | https://arxiv.org/ | Papers | Should work |

### 5. Pagination-heavy
| Site | URL | Type | Expected |
|------|-----|------|----------|
| HN pages | https://news.ycombinator.com/news?p=2 | URL param | Works |
| Reddit pages | https://old.reddit.com/r/stocks/?count=25&after=... | URL param | Should work |
| Search results | various | Next button | Test needed |

### 6. Infinite scroll / AJAX
| Site | URL | Type | Expected |
|------|-----|------|----------|
| Twitter/X | https://x.com/ | Infinite | Blocked + infinite |
| Pinterest | https://pinterest.com/ | Infinite | Needs scroll |
| Instagram | https://instagram.com/ | Infinite | Auth + infinite |

---

## Test Protocol

For each site, record:
1. **Status**: Works / Partial / Blocked / Timeout
2. **Pages crawled**: Number
3. **Documents stored**: Number
4. **Content quality**: Good / Low / Empty
5. **Errors**: Any specific errors
6. **Notes**: Special handling needed

---

## Quick Test Commands

```bash
# Test a site with natural language
uv run webcollector collect "get headlines from <site>" --auto-approve

# Test with a plan file
uv run webcollector collect --plan-file crawl_plans/<plan>.yaml --auto-approve

# Check results
uv run webcollector list-runs
uv run webcollector report <run-id>
uv run webcollector export <run-id> --format jsonl --output results.jsonl
```

---

## Results Log

| Date | Site | Category | Status | Pages | Docs | Avg Text | Notes |
|------|------|----------|--------|-------|------|----------|-------|
| 2026-03-03 | Wikipedia | Easy | ✅ Works | 10 | 5 | 12,518 | Good quality, dedup working |
| 2026-03-03 | SEC EDGAR | Easy | ✅ Works | 118 | 55 | 20,597 | Excellent content, 51 deduped |
| 2026-03-03 | Project Gutenberg | Easy | ✅ Works | 139 | 41 | 9,401 | Good book content |
| 2026-03-03 | Lobste.rs | Easy | ✅ Works | 123 | 44 | 1,037 | Link list, some short docs |
| 2026-03-03 | Python Docs | Easy | ✅ Works | 23 | 19 | 10,907 | Great documentation |
| 2026-03-03 | Old Reddit | Easy | ✅ Works | 120 | 43 | 1,398 | r/stocks, 60 deduped |
| 2026-03-03 | Hacker News | Easy | ⚠️ Partial | 23 | 2 | 852 | Low content quality flag |
| 2026-03-03 | GitHub | Medium | ✅ Works | 33 | 22 | 1,286 | Repo pages extracted |
| 2026-03-03 | Dev.to | Medium | ✅ Works | 125 | 105 | 9,119 | Excellent article content |
| 2026-03-03 | Stack Overflow | Medium | ⚠️ Partial | 66 | 2 | 864 | JS rendering, 14 deduped |
| 2026-03-03 | Medium | Medium | ⏳ Running | - | 2 | 154 | JS-heavy, still crawling |
| 2026-03-03 | Twitter/X | Hard | ❌ Blocked | 2 | 1 | 25 | Login wall required |
| 2026-03-03 | Investing.com | Hard | ❌ Timeout | 0 | 0 | - | 60s navigation timeout |
| 2026-03-03 | Yahoo Finance | Hard | ❌ Blocked | 0 | 0 | - | 503 bot detection |
| 2026-03-03 | Reuters | Hard | ❌ Blocked | 0 | 0 | - | 401 session blocked |

## Summary (2026-03-03)

**Easy Category: 6/7 passing (86%)**
- All static HTML sites work excellently
- Hacker News partial due to link-list content detection

**Medium Category: 2/4 passing (50%)**
- GitHub, Dev.to work great with Playwright
- Stack Overflow partial (JS rendering issues, only 2 unique docs)
- Medium still running (JS-heavy paywall site)

**Hard Category: 0/4 passing (0%)**
- Auth walls and anti-bot protection blocking all
- Feature gaps documented in ISSUE_TRACKER.md (#11, #12, #13)

**Content Quality Metrics:**
- Best: SEC EDGAR (20,597 chars avg)
- Good: Wikipedia, Python Docs, Gutenberg, Dev.to (9,000-13,000 chars)
- Adequate: Reddit, Lobste.rs, GitHub (1,000-1,400 chars)
- Poor: HN, SO link pages (800-900 chars - expected for link lists)
