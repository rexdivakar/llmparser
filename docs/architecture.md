# Architecture: Meaningful Blog Scraper

## Overview

A production-quality, minimal-infra blog scraper built on Scrapy. No LLMs. No external databases.
Deterministic extraction that adapts to any blog template.

---

## Components

### 1. Spider (`spiders/blog_spider.py`)

The central orchestrator. A single generic Scrapy spider that:
- Accepts a `start_url` and infers the allowed domain
- Discovers URLs via sitemap.xml first, then BFS crawl
- Scores each page as "article-like" using heuristics
- Triggers Playwright rendering when JS detection heuristics fire
- Yields `ArticleItem` for article pages; logs skipped URLs with reasons

### 2. Link Discovery

**Phase 1 – Sitemap:**
- Requests `/sitemap.xml` and `/sitemap_index.xml` on startup
- Parses `<loc>` tags recursively (handles sitemap index → child sitemaps)
- All sitemap URLs bypass BFS depth limit but respect `max_pages`

**Phase 2 – BFS Crawl:**
- Parses `<a href>` links from each response
- Filters: same host, not excluded path patterns, not already seen
- Priority queue by path depth; depth-first within a level

### 3. URL Normalizer (`blog_scraper/extractors/urlnorm.py`)

- Lowercases scheme and host
- Removes fragment (`#...`)
- Strips tracking parameters (UTM, fbclid, gclid, ref, etc.)
- Removes default ports (80 for http, 443 for https)
- Sorts remaining query parameters for canonical form
- Generates filesystem-safe slugs from URLs

### 4. Deduplication

- `seen_urls: set` maintained on the spider instance
- Before yielding any Request, normalize URL and check the set
- Canonical URL from `<link rel="canonical">` used if present; falls back to normalized request URL
- Playwright-retry requests use `dont_filter=True` and a `playwright_retry` meta flag to avoid infinite loops

### 5. Article Scorer (`blog_scraper/extractors/heuristics.py`)

Scores a (url, html) pair from 0–100. Threshold: ≥35 → article.

**URL signals (+):** `/blog/`, `/post/`, `/article/` paths, date patterns in URL, long slugs
**URL signals (−):** `/tag/`, `/category/`, `/search`, `/page/N`, root paths
**Content signals (+):** high word count, single H1, author meta, date meta, JSON-LD Article type, OG article type
**Content signals (−):** many outbound links (listing pages), pagination rel links, very short text

### 6. JS Detection (`blog_scraper/extractors/heuristics.py`)

`needs_js(html) → bool` based on:
- "Enable JavaScript" message in body text
- JS framework root divs (`#__next`, `#app`, `#root`, `#__nuxt`) + word count < 100
- Script tag count > 8 + word count < 50
- `<noscript>` tags with significant text

### 7. Metadata Extractor (`blog_scraper/extractors/metadata.py`)

Priority: **JSON-LD** > **Open Graph** > **Twitter Card** > **HTML `<meta>`**

Extracted fields: title, author, published_at, updated_at, description, site_name,
canonical_url, language, tags/categories, images.

### 8. Main Content Extractor (`blog_scraper/extractors/main_content.py`)

Three-tier cascade:

1. **readability-lxml** — Mozilla Readability algorithm port; returns cleaned article HTML
2. **trafilatura** — Second opinion; handles more template varieties
3. **DOM Heuristic** — Scores block elements by paragraph density; falls back to `<article>` / `<main>` / `[role=main]` selectors; last resort: `<body>`

Returns `(content_html: str, method: str)`.

### 9. Block Converter (`blog_scraper/extractors/blocks.py`)

Converts extracted HTML into structured content blocks:
`heading | paragraph | image | code | list | quote | table`

Each block is a dict with `type` + type-specific fields.
Images include `srcset` fallback and `figcaption` discovery.

### 10. Markdown Converter (`blog_scraper/extractors/markdown.py`)

`markdownify` with ATX headings, code fence preservation (language detection from `class="language-X"`), table support.
Post-processes to remove excessive blank lines.

### 11. Middlewares (`blog_scraper/middlewares.py`)

- **RotatingUserAgentMiddleware** – Cycles through a pool of realistic browser UAs
- **PlaywrightFallbackMiddleware** – (Unused at download level; JS fallback is handled in spider parse callback via re-request with `meta['playwright']=True`)

### 12. Pipelines (`blog_scraper/pipelines.py`)

Ordered by priority:

| Priority | Pipeline | Responsibility |
|---|---|---|
| 200 | `ArticleValidationPipeline` | Validate via Pydantic; drop invalid items |
| 300 | `ArticleWriterPipeline` | Write `out/articles/<slug>.json` + `out/articles/<slug>.md` |
| 400 | `IndexWriterPipeline` | Accumulate summaries; write `out/index.json` on spider close |

### 13. CLI (`blog_scraper/__main__.py`)

`python -m blog_scraper --url URL [options]`
Uses `rich` for progress display, `argparse` for options, `CrawlerProcess` to launch Scrapy.

---

## Data Flow

```
start_url
    │
    ▼
[Spider.start_requests]
    ├─► try /sitemap.xml → parse_sitemap → queue URLs
    └─► fetch start_url → parse()
                              │
                              ▼
                    [URL normalization + dedup]
                              │
                              ▼
                    [needs_js(html)?] ──yes──► re-request with Playwright
                              │ no
                              ▼
                    [article_score(url, html)]
                              │
                        score ≥ 35?
                         yes │         │ no
                             ▼         ▼
                    [extract_article] [skip + log]
                             │
                    ┌────────┼─────────┐
                    ▼        ▼         ▼
              metadata  main_content  links
                    └────────┬─────────┘
                             ▼
                       ArticleItem
                             │
                             ▼
                    [ValidationPipeline]
                             │
                    [ArticleWriterPipeline]
                      ├─ out/articles/<slug>.json
                      └─ out/articles/<slug>.md
                             │
                    [IndexWriterPipeline]
                      └─ out/index.json (on close)
```

---

## When Playwright Is Invoked

Playwright is invoked **only** when ALL of the following are true:
- `--render-js` is `auto` (default) or `always`
- The response has NOT already been rendered by Playwright (no `playwright_retry` flag)
- `needs_js(html)` returns True (OR `--render-js always`)

This avoids the overhead of browser launch for the vast majority of static HTML blogs.

When Playwright renders a page:
- Uses `chromium` headless
- Waits for `networkidle` state
- Extracts `page.content()` (fully rendered HTML)
- Re-runs the same extraction pipeline on the rendered HTML

---

## URL Normalization + Dedup Strategy

1. `urlparse` the URL
2. Lowercase scheme + host
3. Remove default port
4. Strip known tracking query params
5. Sort remaining query params
6. Remove URL fragment
7. `seen_urls.add(normalized)` before yielding request
8. After extraction: prefer `<link rel="canonical">` as the canonical URL stored in output

---

## Performance and Failure Modes

**Performance:**
- `AUTOTHROTTLE_ENABLED = True` — adaptive rate limiting
- `CONCURRENT_REQUESTS = 8` default; tunable via `--concurrency`
- Playwright only for JS-heavy pages (typically <5% of a blog)
- `DOWNLOAD_TIMEOUT = 30s`
- Sitemap pre-fetches all article URLs without crawling listing pages

**Failure modes:**
- **Network errors** → Scrapy retry middleware (3 retries, exponential backoff on 429)
- **Readability fails** → falls through to trafilatura → DOM heuristic (never hard-fails)
- **Playwright unavailable** → spider degrades gracefully with warning; falls back to static HTML
- **Sitemap missing/invalid** → silently ignores (errback); BFS crawl continues normally
- **robots.txt disallows** → URL skipped with log entry (obeyed by default)
- **Empty extraction** → item dropped in validation pipeline; URL logged to `out/skipped.jsonl`
- **Duplicate canonical** → second occurrence dropped; first wins
