# Blog Scraper

A production-quality, minimal-infra blog scraper that crawls entire blog domains and extracts structured, meaningful article content — **without any LLMs or external databases**.

Works on any blog, regardless of its framework, template, or technology stack.

---

## Features

- **Generic extraction** — readability-lxml → trafilatura → DOM heuristics cascade; no site-specific selectors
- **Polite crawling** — respects `robots.txt`, auto-throttle, configurable concurrency
- **JS rendering fallback** — automatically detects and renders JS-heavy pages via Playwright (only when needed)
- **Structured output** — JSON + Markdown per article, with an index file
- **Rich metadata** — JSON-LD, Open Graph, Twitter Card extraction with date normalization
- **Content blocks** — heading, paragraph, image, code, list, quote, table
- **No LLMs** — fully deterministic; no API keys, no embeddings

---

## Quickstart

### 1. Install

```bash
pip install -e ".[dev]"

# Install Playwright browser (only needed for JS-rendered sites)
playwright install chromium
```

### 2. Run

```bash
python -m blog_scraper --url https://example.com/blog --out ./out
```

### 3. View results

```
out/
├── articles/
│   ├── blog-my-first-post.json    # Full structured data
│   ├── blog-my-first-post.md      # Readable Markdown
│   └── ...
├── index.json                     # Summary of all articles
└── skipped.jsonl                  # URLs skipped with reasons
```

---

## CLI Reference

```
python -m blog_scraper --url URL [options]

Required:
  --url URL              Blog root URL or any article URL

Options:
  --out DIR              Output directory (default: ./out)
  --max-pages N          Maximum pages to scrape (default: 500)
  --max-depth N          Maximum BFS crawl depth (default: 10)
  --concurrency N        Concurrent requests (default: 8)
  --render-js {auto,always,never}
                         JavaScript rendering mode (default: auto)
  --ignore-robots        Ignore robots.txt
  --include-regex PAT    Only crawl URLs matching PAT
  --exclude-regex PAT    Skip URLs matching PAT
  --log-level {DEBUG,INFO,WARNING,ERROR}
                         Logging verbosity (default: INFO)
```

### Examples

```bash
# Scrape a WordPress blog (politely)
python -m blog_scraper --url https://myblog.com/ --max-pages 100

# Scrape a Next.js blog (force JS rendering)
python -m blog_scraper --url https://modernblog.io/ --render-js always

# Only scrape posts (not pages/categories)
python -m blog_scraper --url https://blog.example.com/ \
  --include-regex '/posts?/' \
  --max-pages 200

# Debug mode with verbose logging
python -m blog_scraper --url https://example.com/blog \
  --log-level DEBUG --max-pages 10

# Ignore robots.txt (use responsibly!)
python -m blog_scraper --url https://example.com/blog --ignore-robots
```

---

## How It Works

### URL Discovery

1. **Sitemap first**: Fetches `/sitemap.xml` and `/sitemap_index.xml` on startup; parses all `<loc>` URLs recursively. This is the fastest path for well-structured blogs.
2. **BFS crawl**: Follows internal `<a href>` links breadth-first up to `--max-depth`. Deduplication via normalized URLs.

### Article Detection

Every crawled page is scored 0–100 by deterministic heuristics:

| Signal | Effect |
|--------|--------|
| URL contains `/blog/`, `/post/`, `/article/` | +15 |
| Date pattern in URL (`/2024/01/post`) | +10 |
| Word count > 300 | +20 |
| Single `<h1>` | +15 |
| JSON-LD `@type: Article` | +10 |
| URL contains `/tag/`, `/category/` | −30 |
| Very short text (< 50 words) | −20 |

Pages scoring ≥ 35 are treated as articles and extracted.

### Content Extraction (three-tier cascade)

1. **readability-lxml** (Mozilla Readability algorithm) — best for most blogs
2. **trafilatura** — second opinion; handles unusual templates well
3. **DOM heuristic** — scores all `<div>` and `<section>` elements by paragraph density; falls back to `<article>`, `<main>`, `[role=main]`

### JS Rendering

With `--render-js auto` (default), Playwright is triggered only when:
- The page has a JS framework root div (`#__next`, `#app`, `#root`, etc.) **and** visible text is sparse
- An "Enable JavaScript" message is detected in the body
- 9+ external scripts loaded with < 50 visible words

This means Playwright is **not invoked** for the vast majority of static HTML blogs, keeping the scraper fast.

### Metadata Priority Chain

```
JSON-LD → Open Graph → Twitter Card → <meta> tags → <title> / <html lang>
```

Dates are normalized to ISO 8601 via `dateparser` (handles "January 15, 2024", "2 days ago", etc.).

---

## Output Schema

### `out/articles/<slug>.json`

```json
{
  "url": "https://example.com/blog/post",
  "canonical_url": "https://example.com/blog/post",
  "title": "Article Title",
  "author": "Jane Smith",
  "published_at": "2024-01-15T10:00:00+00:00",
  "updated_at": null,
  "site_name": "Example Blog",
  "language": "en",
  "tags": ["python", "scraping"],
  "summary": "A brief description.",
  "content_markdown": "# Article Title\n\n...",
  "content_text": "Article Title A brief description...",
  "content_blocks": [
    {"type": "heading", "level": 1, "text": "Article Title"},
    {"type": "paragraph", "text": "First paragraph..."},
    {"type": "code", "language": "python", "text": "import scrapy\n..."}
  ],
  "images": [{"url": "https://...", "alt": "Alt text", "caption": ""}],
  "links": [{"href": "https://...", "text": "Link text", "rel": "", "is_internal": false}],
  "word_count": 842,
  "reading_time_minutes": 5,
  "extraction_method_used": "readability",
  "article_score": 72,
  "scraped_at": "2024-02-24T12:00:00+00:00",
  "raw_metadata": {"jsonld": {}, "og": {}, "twitter": {}}
}
```

See [docs/schema.md](docs/schema.md) for the full schema specification.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
blog_scraper/          # Python package
├── __init__.py
├── __main__.py        # CLI entry point
├── items.py           # Scrapy Items + Pydantic validation schema
├── settings.py        # Scrapy settings
├── middlewares.py     # Rotating UA + Playwright logging
├── pipelines.py       # Validation → JSON writer → Index writer
└── extractors/
    ├── metadata.py    # JSON-LD, OG, Twitter Card extraction
    ├── main_content.py # readability → trafilatura → DOM heuristic
    ├── blocks.py      # HTML → typed content blocks
    ├── markdown.py    # HTML → Markdown
    ├── urlnorm.py     # URL normalization + slug generation
    └── heuristics.py  # Article scoring + JS detection

spiders/
└── blog_spider.py     # Generic domain spider

docs/
├── architecture.md    # Component design and data flow
├── schema.md          # Output JSON schema
└── heuristics.md      # Scoring rules reference

tests/
├── fixtures/          # Sample HTML for unit tests
├── test_extractors.py # Metadata, scoring, extraction tests
└── test_urlnorm.py    # URL normalization tests
```

---

## Limitations

- **Single domain only** — the spider stays within the `start_url` domain; does not follow cross-domain links
- **Login-gated content** — cannot scrape content behind authentication walls
- **Infinite scroll** — only the initial load is captured without `--render-js always`; true infinite scroll pages may need custom `playwright_page_methods`
- **Rate limits / CAPTCHAs** — auto-throttle helps but does not solve hard rate limits; use `--concurrency 1 --max-pages 50` for aggressive rate-limited sites
- **Very dynamic SPAs** — if content changes after user interaction, only the initial render is captured

---

## Configuration Reference

See [docs/architecture.md](docs/architecture.md) for full architectural details and [docs/heuristics.md](docs/heuristics.md) for all scoring rules.
