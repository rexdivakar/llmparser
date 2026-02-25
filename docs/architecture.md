# Architecture: LLMParser

## Overview

A production-quality, minimal-infra web content extractor built on Scrapy. No LLMs. No external databases.
Extracts clean Markdown, typed content blocks, and rich metadata from any website so language models
can read and reason about a site's key components — including JavaScript SPAs, cookie-walled pages,
and sites with paginated archives or accordion/collapsible content.

---

## Components

### 1. Spider (`spiders/blog_spider.py`)

The central crawl orchestrator. A single generic Scrapy spider that:

- Accepts a `start_url` and infers the allowed domain (extendable via `--allow-subdomains` / `--extra-domains`)
- Discovers URLs via sitemap → `<link rel="next">` pagination → BFS `<a href>` crawl
- Scores each page using deterministic heuristics; extracts articles scoring ≥ 35
- Triggers Playwright rendering when the adaptive JS detection heuristics fire
- Supports incremental resume: pre-loads seen URLs from `seen_urls.txt` AND `index.json`
- Yields `ArticleItem` for article pages; logs skipped URLs with reasons to `out/skipped.jsonl`

### 2. Link Discovery

**Phase 1 – Sitemap:**
- Requests `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap-index.xml` on startup
- Parses `<loc>` tags recursively (handles sitemap index → child sitemaps)
- All sitemap URLs bypass BFS depth limit but respect `max_pages`

**Phase 2 – Pagination Links (`<link rel="next">`):**
- `_discover_links()` checks `<head>` for `<link rel="next">` before scanning `<a>` tags
- Matched pagination URLs are enqueued at priority 5 (vs default 0) so archives are fully traversed

**Phase 3 – BFS Crawl:**
- Parses `<a href>` links from each response
- Filters: same host, not excluded path patterns, not already seen
- `--include-regex` restricts extraction but not link discovery (navigation pages still crawled for outbound links)

### 3. URL Normalizer (`llmparser/extractors/urlnorm.py`)

- Lowercases scheme and host
- Removes fragment (`#...`)
- Strips tracking parameters (UTM, fbclid, gclid, ref, etc.)
- Removes default ports (80 for http, 443 for https)
- Sorts remaining query parameters for canonical form
- Generates filesystem-safe slugs from URL paths

### 4. Deduplication

Two-level deduplication:

**Within-crawl:** `seen_urls: set` on the spider instance. Before yielding any Request, normalize URL and check the set.

**Cross-crawl (--resume):** On startup, `_load_seen_urls()` loads:
1. `out/seen_urls.txt` — normalized URLs from the previous incremental run
2. `out/index.json` — URLs of already-extracted articles from any past crawl

Canonical URL from `<link rel="canonical">` used where present.
Playwright-retry requests use `dont_filter=True` + `playwright_retry` meta flag.

**Content dedup:** `ContentHashDedupPipeline` (priority 100) drops articles whose first 5,000 chars of `content_text` hash to a previously-seen SHA-256 prefix (handles syndicated posts / canonical mismatches).

### 5. Adaptive Page Classifier (`llmparser/extractors/adaptive.py`)

Classifies each page before fetching it, selecting the optimal strategy:

| Page Type | Signals | Strategy |
|-----------|---------|----------|
| `STATIC_HTML` | Body words ≥ 150, no JS framework, no overlay | Static HTTP (urllib) |
| `JS_SPA` | Framework root (`#__next`, `#app`, `#root`, `#__nuxt`, etc.) + sparse text; OR ultra-thin body (<10 words) + external scripts | Playwright |
| `JS_SPA` with AMP | Above + `<link rel="amphtml">` | Fetch AMP URL directly |
| `COOKIE_WALLED` | Cookie/GDPR overlay detected in visible text | Playwright |
| `PAYWALLED` | Paywall keywords + metered content signals | Playwright (best-effort) |
| `UNKNOWN` | Low confidence | Mobile UA retry → static |

Classification result is embedded in `article.raw_metadata["_classification"]` (confidence, reason, detected frameworks, AMP URL, feed URL, body word count) so callers display analysis without a second HTTP request.

### 6. Playwright 4-Phase Rendering (`llmparser/query.py :: _fetch_html_playwright`)

For JS-heavy pages:

1. **Phase 1 — Load:** `page.goto(url, wait_until="load")` — HTML + synchronous scripts ready
2. **Phase 2 — Network idle:** `wait_for_load_state("networkidle", timeout=12s)` — Angular / React / Vue XHR bootstrap
3. **Phase 3 — DOM hydration:** `wait_for_function(body.innerText > 50 words, timeout=12s)` — catches SPAs that stream via WebSocket/long-poll after networkidle
4. **Phase 4 — Accordion expansion:** JavaScript `evaluate()` clicks all collapsed sections:
   - `[aria-expanded="false"]` (ARIA-based, most frameworks)
   - `<details>` not yet open (native HTML5)
   - `mat-expansion-panel` (Angular Material)
   - `.collapse:not(.show)` (Bootstrap)
   - Followed by a 6 s networkidle wait if any sections were expanded

All phases are guarded by try/except and degrade gracefully on timeout.

### 7. Article Scorer (`llmparser/extractors/heuristics.py`)

Scores a (url, html) pair from 0–100. Threshold: **≥ 35** → article.

See [heuristics.md](heuristics.md) for the full scoring table.

### 8. JS Detection (`llmparser/extractors/heuristics.py`)

`needs_js(html) → bool` — legacy signal-based check used by the Scrapy spider (not the adaptive engine). Triggered by:
- Explicit "Enable JavaScript" message in body text
- JS framework root div + word count < 100
- 9+ external scripts + word count < 50
- Meaningful `<noscript>` content

### 9. Metadata Extractor (`llmparser/extractors/metadata.py`)

Priority: **JSON-LD** > **Open Graph** > **Twitter Card** > **HTML `<meta>`**

Extracted fields: title, author, published_at, updated_at, description, site_name, canonical_url, language, tags/categories, images.

Dates normalized to ISO 8601 via `dateparser`.

### 10. Main Content Extractor (`llmparser/extractors/main_content.py`)

**Pre-processing** (before any extractor runs):
- Strips `<template>` elements via regex (lxml re-parents template children; decompose alone is insufficient)
- Removes 30+ named cookie-consent / GDPR overlay selectors
- Keyword sweep for dynamically-named consent widgets

**Best-of-two cascade:**
1. Run **readability-lxml** AND **trafilatura** independently
2. If trafilatura returns ≥ 1.4× more words → prefer it (multi-section service pages where readability fixates on one block)
3. Otherwise readability wins (cleaner output for typical blog posts)
4. **DOM heuristic** fallback — paragraph-density scoring across all `<div>`/`<section>`; returns full stripped `<body>` when no single element holds ≥ 55% of body words

### 11. RSS/Atom Feed Parser (`llmparser/extractors/feed.py`)

`parse_feed(xml_text, base_url) → list[FeedEntry]`

- Auto-detects RSS 2.0 vs Atom 1.0 from root element
- RSS: `<channel>/<item>` with Dublin Core namespace support for author/date
- Atom: `<feed>/<entry>` with or without explicit namespace prefix
- Returns `FeedEntry(url, title, author, published_at, summary)` namedtuples

### 12. Block Converter (`llmparser/extractors/blocks.py`)

Converts extracted HTML into structured content blocks:
`heading | paragraph | image | code | list | quote | table`

Each block is a dict with `type` + type-specific fields.
Images include `srcset` fallback and `figcaption` discovery.

### 13. Markdown Converter (`llmparser/extractors/markdown.py`)

`markdownify` with ATX headings, code fence preservation (language detection from `class="language-X"`), table support.
Post-processes to remove excessive blank lines.

### 14. Middlewares (`llmparser/middlewares.py`)

- **RotatingUserAgentMiddleware** — cycles through a pool of realistic browser UAs
- **PlaywrightFallbackMiddleware** — JS fallback handled in spider parse callback via re-request with `meta['playwright']=True`

### 15. Pipelines (`llmparser/pipelines.py`)

Ordered by priority:

| Priority | Pipeline | Responsibility |
|----------|----------|----------------|
| 100 | `ContentHashDedupPipeline` | Drop articles with duplicate content (SHA-256 of first 5,000 chars) |
| 200 | `ArticleValidationPipeline` | Validate via Pydantic; drop items with < 10 words or validation errors |
| 300 | `ArticleWriterPipeline` | Write `out/articles/<slug>.json` + `out/articles/<slug>.md` |
| 400 | `IndexWriterPipeline` | Stream entries to temp JSONL; sort by date; write `out/index.json` + `out/index.csv` on close |

### 16. Query / Batch API (`llmparser/query.py`)

Public Python API (no Scrapy dependency):

| Function | Description |
|----------|-------------|
| `fetch(url)` | Adaptive single-URL fetch + full extraction |
| `fetch_batch(urls, *, max_workers, on_error)` | Concurrent `ThreadPoolExecutor` fetch; results in input order |
| `fetch_feed(feed_url, *, max_articles)` | Parse RSS/Atom feed; fetch + extract each linked article |
| `fetch_html(url)` | Raw HTML string (retries with Retry-After support) |
| `extract(html, url)` | Pure extraction pipeline (no network) |

### 17. CLI (`llmparser/__main__.py`)

`python -m llmparser --url URL [options]`

Key features:
- Rich configuration panel on startup
- Regex validation for `--include-regex` / `--exclude-regex` before crawl starts
- Conditional Playwright setup (`--render-js auto/always/never`)
- HTTP response cache (`--cache`)
- Live Rich progress bar (`--progress`)
- Rich summary table + `out/summary.txt` report after crawl

---

## Data Flow

```
start_url
    │
    ▼
[Spider.start_requests]
    ├─► try /sitemap.xml → parse_sitemap → queue article URLs
    └─► fetch start_url → parse()
                              │
                              ▼
                    [URL normalize + dedup]
                    (seen_urls.txt + index.json on --resume)
                              │
                              ▼
                    [adaptive_fetch_html(url)]
                    ├─ classify_page()
                    │     → Static / SPA / AMP / CookieWall / Unknown
                    ├─ select strategy
                    └─ fetch with chosen strategy
                              │
                              ▼
                    [article_score(url, html)]
                              │
                        score ≥ 35?
                         yes │              │ no
                             ▼              ▼
                   [extract_article]   [skip + log]
                             │
                    ┌────────┼────────────┐
                    ▼        ▼            ▼
              metadata  main_content   links
                 (JSON-LD→OG→meta)  (best-of-two
                                    + DOM fallback)
                    └────────┬────────────┘
                             ▼
                       ArticleItem
                             │
                             ▼
               [ContentHashDedupPipeline]   ← drop if duplicate body
                             │
               [ArticleValidationPipeline]  ← drop if invalid/empty
                             │
               [ArticleWriterPipeline]
                 ├─ out/articles/<slug>.json
                 └─ out/articles/<slug>.md
                             │
               [IndexWriterPipeline]
                 ├─ out/index.json  (on close, sorted by date)
                 └─ out/index.csv   (on close, same data)
```

---

## Retry and Backoff

`fetch_html()` retries on HTTP 429 / 500 / 502 / 503 / 504 and network errors:

```
delay = max(Retry-After header, 2^attempt) + random(0, 1)  seconds
max_retries = 3
```

- `Retry-After` header is read from `urllib.error.HTTPError.headers` and honoured
- Jitter (random 0–1 s) prevents thundering herd on simultaneous retries

---

## When Playwright Is Invoked

**Via `query.fetch()` (adaptive engine):**
- `classify_page()` returns `JS_SPA` or `COOKIE_WALLED`
- AMP URL is not available

**Via spider (`--render-js auto`):**
- `needs_js(html)` returns True AND the URL has not already been Playwright-rendered

**Via `fetch(url, render_js=True)`:**
- Always uses Playwright, skipping classification

---

## URL Normalization + Dedup Strategy

1. `urlparse` the URL
2. Lowercase scheme + host
3. Remove default port (80/443)
4. Strip known tracking query params (utm_*, fbclid, gclid, ref, etc.)
5. Sort remaining query params
6. Remove URL fragment
7. `seen_urls.add(normalized)` before yielding request
8. On `--resume`: also pre-load from `seen_urls.txt` + `index.json`
9. After extraction: prefer `<link rel="canonical">` as the stored canonical URL

---

## Performance and Failure Modes

**Performance:**
- `AUTOTHROTTLE_ENABLED = True` — adaptive rate limiting
- `CONCURRENT_REQUESTS = 8` default; tunable via `--concurrency`
- Playwright only for JS-heavy pages (typically < 5% of a blog)
- HTTP cache (`--cache`) eliminates repeated fetches during development
- Sitemap pre-fetches all article URLs without crawling listing pages

**Failure modes:**

| Failure | Handling |
|---------|----------|
| HTTP 429/5xx | Retry with Retry-After / exponential backoff (max 3 retries) |
| readability fails | Falls through to trafilatura → DOM heuristic (never hard-fails) |
| Playwright unavailable | Warning logged; spider falls back to static HTML extraction |
| Sitemap missing/invalid | Silently ignored (errback); BFS crawl continues normally |
| robots.txt disallows | URL skipped with log entry (obeyed by default) |
| Empty extraction (< 10 words) | Item dropped in validation pipeline; logged to `out/skipped.jsonl` |
| Duplicate content | Dropped by ContentHashDedupPipeline; first occurrence wins |
| `<template>` GDPR overlays | Stripped via regex before any HTML parsing |
