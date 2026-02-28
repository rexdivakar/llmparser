# LLMParser

**Turn any website into clean, structured content that language models can actually read.**

LLMParser extracts articles, documentation, and blog posts from the web and delivers them as
clean Markdown, typed content blocks, and normalised metadata — ready to drop into LLM prompts,
RAG pipelines, or knowledge bases. No LLMs needed to run it. No API keys. No databases.

[![PyPI version](https://badge.fury.io/py/llmparser.svg)](https://pypi.org/project/llmparser/)
[![Python](https://img.shields.io/pypi/pyversions/llmparser.svg)](https://pypi.org/project/llmparser/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Why LLMParser?

Raw HTML is 80% noise. Navigation bars, cookie banners, sidebars, ad scripts, and boilerplate
drown out the 20% of content that actually matters. Feeding raw HTML to an LLM wastes tokens,
degrades answer quality, and blows context windows.

LLMParser solves this:

| Problem | LLMParser Solution |
|---------|-------------------|
| Raw HTML is noisy | Removes nav, footer, ads, cookie overlays, sidebars |
| JS SPAs return empty HTML | 4-phase Playwright rendering with accordion expansion |
| Metadata scattered across 4 formats | Unified: JSON-LD → OG → Twitter Card → `<meta>` |
| Dates in 30+ formats | Normalised to ISO 8601 via `dateparser` |
| Sites change structure | Zero site-specific selectors — fully generic extraction |
| Need LLM-ready chunks | Typed blocks: `heading / paragraph / code / list / quote / table` |

---

## LLMParser vs Firecrawl

The two tools solve different problems at different levels.

| | LLMParser | Firecrawl |
|---|---|---|
| **Type** | Local Python package | Cloud API service |
| **Cost** | Free (you run it) | Paid (per page/crawl) |
| **Privacy** | Data never leaves your machine | Pages sent to Firecrawl servers |
| **Rate limits** | None — your own infra | Plan-based (capped free tier) |
| **LLM used** | No — fully deterministic | Yes — LLM extraction mode |
| **Open source** | Yes, fully | Partial (SDK open, core closed) |
| **Full site crawl** | Built-in (BFS + sitemap + pagination) | Premium feature |
| **RSS/feed support** | Yes — `fetch_feed()` native | No |
| **Typed content blocks** | Yes — `heading / paragraph / code / list / quote / table` | No — raw Markdown only |
| **Article scoring** | Yes — discards nav/tag/pagination pages automatically | No |
| **Offline / air-gapped** | Yes | No |

### Where Firecrawl wins
- Zero setup — one API call, no Playwright install
- LLM-powered schema extraction — define a JSON schema, AI fills it from any page
- Managed anti-bot bypass — residential proxies, fingerprint spoofing
- Actions API — click, scroll, fill forms before scraping

### Where LLMParser wins
- **Free at any scale** — 100,000 pages costs $0 vs a paid Firecrawl plan
- **Deterministic** — same input always gives same output; no hallucination risk in extraction
- **Richer structure** — typed blocks, unified metadata, ISO 8601 dates, article scoring
- **Full crawl pipeline** — BFS + sitemap + pagination + resume + dedup out of the box
- **Data ownership** — nothing leaves your machine; works in air-gapped environments

### In one sentence

> Firecrawl is a managed scraping API with LLM extraction. LLMParser is a self-hosted crawl engine that produces richer structured output at zero marginal cost — but you own the infrastructure.

**Use Firecrawl** for one-off scraping, heavy anti-bot sites, or LLM-powered field extraction from a handful of URLs.

**Use LLMParser** when cost at scale matters, data privacy is required, or you need a full site ingestion pipeline for RAG.

---

## What Makes It Different

### Adaptive Engine — Reads Any Site, Dynamically

Most scrapers break when a site changes its template or uses a JS framework. LLMParser
classifies each page before fetching it and picks the right strategy automatically:

```
Static HTML  → urllib (fast, no browser)
JS SPA       → Playwright (4-phase rendering, accordion expansion)
AMP version  → fetch lightweight AMP URL directly
Cookie wall  → Playwright clicks through GDPR overlays
Unknown      → mobile User-Agent retry → graceful fallback
```

The classification result (page type, confidence, detected frameworks, AMP URL) is embedded
in the output so you always know how the content was fetched.

### Playwright 4-Phase Rendering

For JavaScript-heavy pages, LLMParser doesn't just "wait for the page to load" — it:

1. **Load** — HTML and synchronous scripts ready
2. **Network idle** — React / Vue / Angular XHR bootstrap completes (up to 12 s)
3. **DOM hydration** — waits until `body.innerText` exceeds 50 words (catches WebSocket/long-poll SPAs)
4. **Accordion expansion** — auto-clicks all collapsed `[aria-expanded="false"]` panels, `<details>`,
   Angular Material expansion panels, and Bootstrap collapsibles — then waits for the expanded
   content to settle

No other open-source scraper does step 4. This is critical for documentation sites, FAQs, and
service portals where key content is hidden behind click-to-expand UI.

### Best-of-Two Content Extraction

Two extractors run independently on every page:

- **readability-lxml** — great for blog posts with a clear main content div
- **trafilatura** — better for multi-section service pages

LLMParser picks the winner by word count: trafilatura wins only if it returns ≥ 40% more words
(avoiding noisy ties). If both fail, a paragraph-density DOM heuristic finds the richest element
and falls back to the full stripped `<body>` — so you always get something.

### Zero Site-Specific Code

There are no custom selectors for Medium, Substack, Dev.to, or any other platform. Every rule
is generic HTML semantics — which means LLMParser works on sites that don't exist yet.

---

## Features

- **Adaptive engine** — classifies each page (Static HTML, JS SPA, Cookie-walled, Paywalled) and selects the best fetch strategy automatically
- **Generic extraction** — readability-lxml + trafilatura best-of-two → DOM heuristic cascade; zero site-specific selectors
- **Playwright 4-phase rendering** — load → networkidle → DOM hydration → accordion expansion
- **RSS/Atom feed support** — `fetch_feed()` parses any feed and extracts each linked article
- **Feed discovery** — automatically probes common feed endpoints and `<link rel="alternate">` feeds in crawls
- **Pagination auto-follow** — detects `<link rel="next">` and traverses paginated archives
- **Incremental resume** — skip previously-seen URLs; cross-crawl dedup via `index.json`
- **Concurrent batch API** — `fetch_batch()` fetches multiple URLs in parallel
- **Block-aware proxy retry** — retries on bot blocks and HTTP 401/403/407 when proxies are provided
- **Auth-ready** — inject headers/cookies and refresh bearer tokens for 401s
- **Per-domain rate limiting** — configurable RPS for single URL and batch fetches
- **Delta mode** — conditional requests using ETag/Last-Modified (spider)
- **Telemetry export** — `out/telemetry.json` with response codes, latency, block rate
- **Crawl profiles** — YAML per-domain config for limits, auth, and Playwright actions
- **Language fallback** — auto-detect language when metadata is missing
- **Polite crawling** — robots.txt, auto-throttle, Retry-After header support
- **Structured output** — JSON + Markdown per page; `index.json` + `index.csv` summaries
- **Native RAG integration** — `chunk_article()` with paragraph/fixed/sentence strategies; LangChain + LlamaIndex adapters; JSONL export for Pinecone/Chroma/Weaviate/Qdrant
- **Plugin architecture** — extend fetch strategies, content extractors, article scorers, and output formatters without forking the library
- **No LLMs** — fully deterministic extraction; no API keys, no embeddings

---

## Quickstart

### 1. Install

```bash
pip install llmparser

# Only needed for JS-rendered sites
playwright install chromium

# Optional: RAG framework adapters
pip install "llmparser[rag]"  # installs langchain-core + llama-index-core
```

### 2. Single URL (Python API)

```python
from llmparser import fetch

article = fetch("https://example.com/blog/post")

# Ready for LLM context
print(article.content_markdown)

# Typed blocks (heading/paragraph/code/list/quote/table/image)
for block in article.content_blocks:
    print(block["type"], block.get("text", ""))

# Rich metadata
print(article.title, article.author, article.published_at)
```

### 3. Full site crawl (CLI)

```bash
python -m llmparser --url https://example.com/blog --out ./out
```

### 4. View results

```
out/
├── articles/
│   ├── blog-my-first-post.json    # Full structured data
│   ├── blog-my-first-post.md      # Clean Markdown (LLM-ready)
│   └── ...
├── index.json                     # Summary of all pages (sorted by date)
├── index.csv                      # Same summary as CSV
├── dedup.jsonl                    # Cross-run dedup hashes (content/title/canonical)
├── etag_cache.json                # ETag/Last-Modified cache (delta mode)
├── skipped.jsonl                  # Skipped URLs with reasons
├── telemetry.json                 # Crawl telemetry (optional)
└── summary.txt                    # Plain-text crawl report
```

`index.json` and `index.csv` include `link_count` (number of extracted hyperlinks per article).
`skipped.jsonl` records explicit skip reasons; `low_article_score (N)` means the page scored below the extraction threshold (`35`).

---

## Python API

### Single URL

```python
from llmparser import fetch

article = fetch("https://example.com/blog/post")
print(article.title)
print(article.author)
print(article.word_count)
print(article.content_markdown)   # clean Markdown

data = article.model_dump()       # plain dict, JSON-serialisable
```

### Auth, rate limit, and Playwright actions

```python
from llmparser import AuthSession, fetch

auth = AuthSession(
    headers={"X-API-Key": "secret"},
    cookies={"sessionid": "abc123"},
)

article = fetch(
    "https://example.com/private/post",
    auth=auth,
    rate_limit_per_domain=2.0,  # 2 requests/sec per domain
    playwright_page_methods=[
        {"method": "evaluate", "args": ["window.scrollTo(0, document.body.scrollHeight)"]},
        {"method": "wait_for_timeout", "args": [1000]},
    ],
)
```

### Force Playwright

```python
article = fetch("https://angular-app.io/blog/post", render_js=True)
```

### Batch (concurrent)

```python
from llmparser import fetch_batch

articles = fetch_batch([
    "https://example.com/post/1",
    "https://example.com/post/2",
], max_workers=4, on_error="skip")

for article in articles:
    print(article.title, article.word_count)
```

### RSS/Atom feed

```python
from llmparser import fetch_feed

articles = fetch_feed("https://example.com/feed.xml", max_articles=50)
for article in articles:
    print(article.title, article.published_at)
```

### Low-level

```python
from llmparser import fetch_html, extract

html = fetch_html("https://example.com/blog/post")
article = extract(html, url="https://example.com/blog/post")
```

---

## RAG Integration

LLMParser has a native chunking layer that turns any `ArticleSchema` into vector-DB-ready
chunks — with rich metadata attached to every chunk.

### Install extras

```bash
pip install "llmparser[rag]"   # installs langchain-core + llama-index-core
```

### Chunk an article

```python
from llmparser import fetch
from llmparser.rag import chunk_article

article = fetch("https://example.com/blog/post")

# Three chunking strategies:
#   "paragraph" — split on heading/paragraph content_blocks (best quality, default)
#   "fixed"     — sliding window of chunk_size chars with overlap
#   "sentence"  — split on sentence endings, then group to chunk_size
chunks = chunk_article(article, strategy="paragraph", chunk_size=512, overlap=50)

for chunk in chunks:
    print(chunk.chunk_id)         # "<slug>_<index>" — stable, reproducible ID
    print(chunk.text)             # chunk text
    print(chunk.metadata)         # url, title, author, published_at, tags, chunk_index, …
```

Each `ArticleChunk` carries:

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | `str` | `<url-slug>_<index>` — stable, reproducible |
| `text` | `str` | Chunk text |
| `chunk_index` | `int` | Position within the article |
| `article_url` | `str` | Source URL |
| `metadata` | `dict` | url, title, author, published_at, tags, site_name, language, chunk_index, chunk_count, word_count |

### LangChain adapter

```python
# Requires: pip install langchain-core
docs = article.to_langchain(strategy="paragraph", chunk_size=512)
# docs is a list of langchain_core.documents.Document
# doc.page_content = chunk text, doc.metadata = chunk metadata

# Or directly:
from llmparser.rag import to_langchain
docs = to_langchain(article)
```

### LlamaIndex adapter

```python
# Requires: pip install llama-index-core
nodes = article.to_llamaindex(strategy="sentence", chunk_size=256)
# nodes is a list of llama_index.core.schema.TextNode
# node.text = chunk text, node.metadata = chunk metadata, node.id_ = chunk_id

from llmparser.rag import to_llamaindex
nodes = to_llamaindex(article)
```

### JSONL export (Pinecone / Chroma / Weaviate / Qdrant)

```python
from llmparser import fetch_batch, to_jsonl

articles = fetch_batch([
    "https://example.com/blog/post-1",
    "https://example.com/blog/post-2",
], max_workers=4, on_error="skip")

# Each line: {"id": "<chunk_id>", "text": "...", "metadata": {...}}
n = to_jsonl(articles, "/tmp/chunks.jsonl", strategy="paragraph", chunk_size=512)
print(f"Wrote {n} chunks")
```

The JSONL format is directly compatible with upsert endpoints for:
- **Pinecone**: `index.upsert_from_dataframe(df)` after loading with pandas
- **Chroma**: `collection.add(ids=[c["id"]], documents=[c["text"]], metadatas=[c["metadata"]])`
- **Weaviate** / **Qdrant**: `client.upsert(collection, points=[{"id": …, "vector": …, "payload": c["metadata"]}])`

### Convenience methods on `ArticleSchema`

```python
article = fetch("https://example.com/blog/post")

chunks = article.to_chunks(strategy="fixed", chunk_size=256)
docs   = article.to_langchain(strategy="paragraph")
nodes  = article.to_llamaindex(chunk_size=512)
```

---

## Plugin Architecture

LLMParser exposes four extension points via `@runtime_checkable` Protocol classes.
Register plugins once at startup; they are automatically used on every subsequent fetch.

### Fetch strategy plugin

Override how a URL is fetched (e.g. authenticated sessions, custom proxies):

```python
from llmparser import register_strategy

class AuthenticatedFetcher:
    name = "my_auth_strategy"

    def can_handle(self, url: str, signals) -> bool:
        return "internal.example.com" in url

    def fetch(self, url: str, timeout: int) -> str:
        import requests
        return requests.get(url, headers={"Authorization": "Bearer …"}, timeout=timeout).text

register_strategy(AuthenticatedFetcher())
```

### Content extractor plugin

Provide a custom HTML → content extractor (higher `priority` is tried first):

```python
from llmparser import register_extractor

class MyExtractor:
    name = "my_extractor"
    priority = 10  # higher than built-in tiers

    def can_extract(self, html: str, url: str) -> bool:
        return "special-site.com" in url

    def extract(self, html: str, url: str) -> str | None:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        main = soup.select_one(".article-body")
        return str(main) if main else None

register_extractor(MyExtractor())
```

### Article scorer plugin

Adjust the article quality score (e.g. boost domain-specific signals):

```python
from llmparser import register_scorer

class TechBooster:
    name = "tech_booster"

    def score(self, url: str, html: str, base_score: int) -> int:
        boost = 10 if "python" in html.lower() else 0
        return base_score + boost

register_scorer(TechBooster())
```

### Output formatter plugin

Write articles in additional formats alongside `.json` and `.md`:

```python
from llmparser import register_formatter

class RstFormatter:
    name = "rst_formatter"
    extension = "rst"  # saves as <slug>.rst next to .json and .md

    def format(self, article) -> str:
        lines = [article.title, "=" * len(article.title), "", article.content_text or ""]
        return "\n".join(lines)

register_formatter(RstFormatter())
```

All four plugin types are tolerance-wrapped — an exception in a plugin is logged as a warning
and the built-in pipeline continues unaffected.

---

## CLI Reference

```
python -m llmparser --url URL [options]

Required:
  --url URL              Site URL to start from

Output:
  --out DIR              Output directory (default: ./out)

Crawl limits:
  --max-pages N          Maximum pages to scrape (default: 500)
  --max-depth N          Maximum BFS crawl depth (default: 10)
  --concurrency N        Concurrent requests (default: 8)

Rendering:
  --render-js {auto,always,never}
                         JavaScript rendering mode (default: auto)

Filtering:
  --ignore-robots        Ignore robots.txt
  --include-regex PAT    Only extract from URLs matching PAT
  --exclude-regex PAT    Skip URLs matching PAT

Domain scope:
  --allow-subdomains     Crawl subdomains of the start URL domain
  --extra-domains DOMS   Comma-separated extra domains to crawl

Resume / caching:
  --resume               Skip URLs already in seen_urls.txt and index.json
  --cache                Enable HTTP response cache for faster re-runs

Display:
  --log-level {DEBUG,INFO,WARNING,ERROR}
  --progress             Show live Rich progress bar
  --telemetry            Write crawl telemetry to out/telemetry.json
  --delta                Conditional requests using ETag/Last-Modified
  --download-delay SEC   Download delay between requests (seconds)
  --no-autothrottle      Disable Scrapy AutoThrottle
  --rate-limit RPS       Per-domain rate limit in requests/sec
  --header HEADER        Extra request header (repeatable)
  --cookie COOKIE        Cookie in name=value form (repeatable)
  --profile FILE         YAML crawl profile (per-domain settings)
  --playwright-actions FILE
                         JSON file with Playwright page method calls
```

### Examples

```bash
# Parse a documentation site for LLM ingestion
python -m llmparser --url https://docs.example.com/ --max-pages 200

# JS SPA (React, Angular, Vue) — auto-detected, Playwright used automatically
python -m llmparser --url https://modernsite.io/blog/ --render-js auto

# Resume a previous crawl (skips already-parsed pages)
python -m llmparser --url https://example.com/ --resume

# Parse only blog posts, skip everything else
python -m llmparser --url https://example.com/ --include-regex '/blog/'

# Multi-domain (main site + docs subdomain)
python -m llmparser --url https://example.com/ \
  --allow-subdomains \
  --extra-domains "docs.example.com"

# Fast development iteration with caching
python -m llmparser --url https://example.com/ \
  --log-level DEBUG --max-pages 10 --cache

# Use a YAML crawl profile
python -m llmparser --url https://example.com/ --profile ./profiles.yaml
```

Example `profiles.yaml`:

```yaml
default:
  max_pages: 200
  max_depth: 8

domains:
  example.com:
    rate_limit: 2.0
    delta: true
    headers:
      Authorization: "Bearer YOUR_TOKEN"
    cookies:
      sessionid: "abc123"
    playwright_actions:
      - method: evaluate
        args: ["window.scrollTo(0, document.body.scrollHeight)"]
      - method: wait_for_timeout
        args: [1000]
```

---

## How It Works

### Adaptive Engine

Before fetching, each page is classified:

| Page Type | Detection | Strategy |
|-----------|-----------|----------|
| Static HTML | Body words ≥ 150, no JS framework | urllib (fast) |
| JS SPA | Framework root (`#__next`, `#app`, etc.) + sparse text; or thin body + external scripts | Playwright |
| AMP available | `<link rel="amphtml">` | Fetch lightweight AMP URL |
| Cookie-walled | GDPR overlay in visible text | Playwright |
| Unknown | Low confidence | Mobile UA retry → static |

Classification signals are stored in `article.raw_metadata["_classification"]`.

### Playwright 4-Phase Rendering

For JS-heavy pages:

1. **Load** — HTML + synchronous scripts ready
2. **Network idle** — React/Vue/Angular XHR bootstrap (up to 12 s)
3. **DOM hydration** — wait for `>50 words` in `body.innerText`
4. **Accordion expansion** — click all `[aria-expanded="false"]`, open `<details>`, expand Angular Material panels and Bootstrap collapsibles

### Content Extraction

1. **readability-lxml** + **trafilatura** run independently
2. If trafilatura returns ≥ 1.4× more words → it wins (multi-section pages)
3. **DOM heuristic** fallback — returns full stripped `<body>` when no single element dominates

Cookie banners and GDPR overlays are removed before any extractor runs.

### Output for LLMs

Each parsed page produces:

- `content_markdown` — clean Markdown, ready to paste into an LLM prompt or RAG chunk
- `content_blocks` — typed structured blocks (heading / paragraph / code / list / quote / table / image)
- `content_text` — plain text for embedding or keyword search
- Full metadata (title, author, date, tags, language, canonical URL)

---

## Output Schema

### `out/articles/<slug>.json`

```json
{
  "url": "https://example.com/blog/post",
  "canonical_url": "https://example.com/blog/post",
  "title": "Page Title",
  "author": "Jane Smith",
  "published_at": "2024-01-15T10:00:00+00:00",
  "language": "en",
  "tags": ["python", "llm"],
  "summary": "Brief description.",
  "content_markdown": "# Page Title\n\n...",
  "content_blocks": [
    {"type": "heading", "level": 1, "text": "Page Title"},
    {"type": "paragraph", "text": "First paragraph..."},
    {"type": "code", "language": "python", "text": "from llmparser import fetch"}
  ],
  "word_count": 842,
  "reading_time_minutes": 5,
  "extraction_method_used": "readability",
  "fetch_strategy": "static",
  "page_type": "static_html",
  "article_score": 72,
  "scraped_at": "2024-02-24T12:00:00+00:00"
}
```

See [docs/schema.md](docs/schema.md) for the full schema.

---

## Running Tests

```bash
pytest tests/ -v
# 121 tests, ~9 s
```

---

## Project Structure

```
llmparser/                  # Core package
├── __init__.py             # Public API: fetch, fetch_batch, fetch_feed, fetch_html, extract,
│                           #   register_strategy/extractor/scorer/formatter, ArticleChunk, to_jsonl
├── __main__.py             # CLI (python -m llmparser)
├── items.py                # Pydantic ArticleSchema (+ to_chunks/to_langchain/to_llamaindex)
├── rag.py                  # RAG chunking: chunk_article, to_langchain, to_llamaindex, to_jsonl
├── plugins.py              # Plugin registry: FetchStrategy/Extractor/Scorer/Formatter protocols
├── settings.py             # Scrapy settings
├── middlewares.py          # Rotating UA + Playwright logging
├── pipelines.py            # ContentHashDedup → Validation → ArticleWriter → IndexWriter (+CSV)
└── extractors/
    ├── adaptive.py         # Page-type classifier + strategy selector (+ strategy plugin hook)
    ├── feed.py             # RSS 2.0 / Atom 1.0 parser
    ├── metadata.py         # JSON-LD, OG, Twitter Card, <meta>
    ├── main_content.py     # readability + trafilatura + DOM heuristic (+ extractor plugin hook)
    ├── blocks.py           # HTML → typed content blocks
    ├── markdown.py         # HTML → Markdown
    ├── urlnorm.py          # URL normalization + slug generation
    └── heuristics.py       # Article scoring + JS detection (+ scorer plugin hook)

spiders/
└── blog_spider.py          # Generic domain spider (sitemap + BFS + pagination)

docs/
├── architecture.md
├── schema.md
└── heuristics.md

tests/
├── fixtures/
├── test_adaptive.py        # 22 tests
├── test_extractors.py      # Metadata, scoring, extraction
├── test_query.py           # fetch / fetch_batch / extract API
└── test_urlnorm.py         # URL normalization
```

---

## Limitations

- **Login-gated content** — cannot parse content behind authentication
- **Hard infinite scroll** — captures initial render only; true infinite scroll needs custom `playwright_page_methods`
- **CAPTCHAs** — auto-throttle helps but does not bypass hard CAPTCHA gates
- **Single domain by default** — use `--allow-subdomains` / `--extra-domains` for multi-domain crawls

---

## License

MIT — see [LICENSE](LICENSE).
