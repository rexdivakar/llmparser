# Output Schema

## File Layout

```
out/
├── articles/
│   ├── <slug>.json          # Full structured data per article
│   ├── <slug>.md            # Markdown rendering of the article
│   └── ...
├── index.json               # Summary list, sorted by published_at descending
├── index.csv                # Same summary as CSV (spreadsheet / pandas ready)
├── skipped.jsonl            # JSONL log of skipped URLs with reasons
└── summary.txt              # Plain-text crawl report
```

Slug is derived from the URL path: `/blog/how-to-scrape` → `blog-how-to-scrape`.
Slug is truncated to 100 characters. Conflicts: append `-2`, `-3`, etc.

---

## Article JSON Schema

Each `out/articles/<slug>.json` conforms to this Pydantic model:

```python
class ArticleSchema(BaseModel):
    # Identity
    url: str                          # Final request URL
    canonical_url: Optional[str]      # From <link rel="canonical"> or og:url

    # Metadata
    title: str                        # Page title (JSON-LD > OG > <title>)
    author: Optional[str]             # Author name
    published_at: Optional[str]       # ISO 8601: "2024-01-15T10:00:00+00:00"
    updated_at: Optional[str]         # ISO 8601
    site_name: Optional[str]          # og:site_name or domain
    language: Optional[str]           # BCP-47: "en", "fr", etc.
    tags: List[str]                   # From article:tag, JSON-LD keywords, etc.
    summary: Optional[str]            # meta description or og:description

    # Content
    content_markdown: str             # Full article in Markdown
    content_text: str                 # Plain text (whitespace-normalized)
    content_blocks: List[Block]       # Structured blocks (see below)

    # Media & links
    images: List[ImageRef]            # All images in article
    links: List[LinkRef]              # All links in article

    # Stats
    word_count: int                   # Words in content_text
    reading_time_minutes: float       # word_count / 200, rounded up

    # Provenance
    extraction_method_used: str       # "readability" | "trafilatura" | "dom_heuristic"
    fetch_strategy: Optional[str]     # "static" | "amp" | "mobile_ua" | "playwright" |
                                      # "playwright_forced" | "playwright_fallback" |
                                      # "static_best_effort"
    page_type: Optional[str]          # "static_html" | "js_spa" | "cookie_walled" |
                                      # "paywalled" | "unknown"
    article_score: int                # 0-100 heuristic score
    scraped_at: str                   # ISO 8601 UTC timestamp

    # Raw signals
    raw_metadata: RawMetadata         # Unprocessed signals (see below)
```

### Block Types

```python
# Heading block
{"type": "heading", "level": 2, "text": "Getting Started"}

# Paragraph block
{"type": "paragraph", "text": "Building a scraper requires..."}

# Image block
{"type": "image", "url": "https://...", "alt": "Diagram", "caption": "Figure 1"}

# Code block
{"type": "code", "language": "python", "text": "import scrapy\n..."}

# List block
{"type": "list", "ordered": false, "items": ["Item 1", "Item 2"]}

# Quote block
{"type": "quote", "text": "The best scrapers work on any site."}

# Table block
{"type": "table", "rows": [["Header A", "Header B"], ["Cell 1", "Cell 2"]]}
```

### ImageRef

```python
{"url": "https://cdn.example.com/img.png", "alt": "Alt text", "caption": "Optional"}
```

### LinkRef

```python
{
  "href": "https://example.com/other-post",
  "text": "Read more",
  "rel": "noopener",
  "is_internal": true
}
```

### RawMetadata

```python
{
  "jsonld": {
    "@type": "Article",
    "headline": "...",
    "author": {"@type": "Person", "name": "..."},
    "datePublished": "2024-01-15T10:00:00Z"
  },
  "og": {
    "og:title": "...",
    "og:description": "...",
    "og:type": "article",
    "article:published_time": "2024-01-15T10:00:00Z"
  },
  "twitter": {
    "twitter:title": "...",
    "twitter:creator": "@author"
  },
  "_classification": {
    "reason": "Static HTML — 842 body words, no JS framework signals",
    "confidence": 0.95,
    "frameworks": [],
    "amp_url": null,
    "feed_url": "/feed.xml",
    "body_word_count": 842
  }
}
```

The `_classification` key is present when `fetch()` uses the adaptive engine (i.e. `render_js=False`, the default). It contains the classification signals used to select the fetch strategy — no second HTTP request is needed to display this information.

---

## Full JSON Example

```json
{
  "url": "https://example.com/blog/extracting-structured-content-for-llms",
  "canonical_url": "https://example.com/blog/extracting-structured-content-for-llms",
  "title": "Extracting Structured Content for LLMs",
  "author": "Jane Smith",
  "published_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-16T08:00:00+00:00",
  "site_name": "Tech Blog",
  "language": "en",
  "tags": ["python", "web-scraping", "scrapy"],
  "summary": "A comprehensive guide to extracting structured, LLM-ready content from any website.",
  "content_markdown": "# Extracting Structured Content for LLMs\n\nLLMParser makes any website readable by language models...\n\n## Getting Started\n\n...",
  "content_text": "Extracting Structured Content for LLMs LLMParser makes any website readable by language models...",
  "content_blocks": [
    {"type": "heading", "level": 1, "text": "Extracting Structured Content for LLMs"},
    {"type": "paragraph", "text": "LLMParser makes any website readable by language models..."},
    {"type": "heading", "level": 2, "text": "Getting Started"},
    {"type": "code", "language": "python", "text": "from llmparser import fetch\n\narticle = fetch('https://example.com/blog/post')\nprint(article.content_markdown)"}
  ],
  "images": [
    {"url": "https://example.com/img/diagram.png", "alt": "Architecture diagram", "caption": "Figure 1"}
  ],
  "links": [
    {"href": "https://scrapy.org", "text": "Scrapy", "rel": "", "is_internal": false}
  ],
  "word_count": 842,
  "reading_time_minutes": 5,
  "extraction_method_used": "readability",
  "fetch_strategy": "static",
  "page_type": "static_html",
  "article_score": 72,
  "scraped_at": "2024-02-24T12:00:00+00:00",
  "raw_metadata": {
    "jsonld": {"@type": "Article", "headline": "Extracting Structured Content for LLMs"},
    "og": {"og:title": "Extracting Structured Content for LLMs", "og:type": "article"},
    "twitter": {},
    "_classification": {
      "reason": "Static HTML — 842 body words",
      "confidence": 0.95,
      "frameworks": [],
      "amp_url": null,
      "feed_url": "/feed.xml",
      "body_word_count": 842
    }
  }
}
```

---

## Markdown Output Format

```markdown
# Extracting Structured Content for LLMs

**Author:** Jane Smith
**Published:** 2024-01-15T10:00:00+00:00
**Tags:** python, web-scraping, scrapy

> A comprehensive guide to extracting structured, LLM-ready content from any website.

---

LLMParser makes any website readable by language models...

## Getting Started

```python
from llmparser import fetch

article = fetch("https://example.com/blog/post")
print(article.content_markdown)
```

```

---

## Index JSON (`out/index.json`)

Array of summary objects, sorted by `published_at` descending (unparseable dates last).
Includes `link_count` (number of extracted hyperlinks in the article body).

```json
[
  {
    "slug": "blog-extracting-structured-content-for-llms",
    "url": "https://example.com/blog/extracting-structured-content-for-llms",
    "title": "Extracting Structured Content for LLMs",
    "author": "Jane Smith",
    "published_at": "2024-01-15T10:00:00+00:00",
    "summary": "A comprehensive guide...",
    "tags": ["python", "web-scraping"],
    "word_count": 842,
    "reading_time_minutes": 5,
    "extraction_method_used": "readability",
    "link_count": 12
  }
]
```

## Index CSV (`out/index.csv`)

Same fields as `index.json`, one row per article.  Load directly with:

```python
import pandas as pd
df = pd.read_csv("out/index.csv")
df.sort_values("word_count", ascending=False).head(10)
```

---

## Skipped URLs Log (`out/skipped.jsonl`)

One JSON object per line:

```jsonl
{"url": "https://example.com/tag/python", "reason": "low_article_score (12)", "timestamp": "2024-02-24T12:00:00+00:00"}
{"url": "https://example.com/page/2", "reason": "excluded_pattern (/page/)", "timestamp": "2024-02-24T12:01:00+00:00"}
{"url": "https://example.com/short", "reason": "content too short (<10 words)", "timestamp": "2024-02-24T12:02:00+00:00"}
```

---

## FeedEntry (from `fetch_feed` / `parse_feed`)

`llmparser.extractors.feed.FeedEntry` is a `NamedTuple`:

```python
class FeedEntry(NamedTuple):
    url: str              # Article URL
    title: str            # Entry title
    author: str | None    # Author (dc:creator in RSS, <author><name> in Atom)
    published_at: str | None  # pubDate (RSS) or <published> (Atom) — raw string
    summary: str | None   # <description> (RSS) or <summary>/<content> (Atom)
```

`published_at` is the raw string from the feed; it is **not** normalized to ISO 8601 at the feed-parse level. After `fetch_feed()` calls `fetch()` on each URL, the resulting `ArticleSchema.published_at` will be normalized by `dateparser`.
