# Output Schema

## File Layout

```
out/
├── articles/
│   ├── <slug>.json          # Full structured data per article
│   ├── <slug>.md            # Markdown rendering of the article
│   └── ...
├── index.json               # Summary list of all scraped articles
└── skipped.jsonl            # JSONL log of skipped URLs with reasons
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
  }
}
```

---

## Full JSON Example

```json
{
  "url": "https://example.com/blog/how-to-build-a-scraper",
  "canonical_url": "https://example.com/blog/how-to-build-a-scraper",
  "title": "How to Build a Blog Scraper",
  "author": "Jane Smith",
  "published_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-16T08:00:00+00:00",
  "site_name": "Tech Blog",
  "language": "en",
  "tags": ["python", "web-scraping", "scrapy"],
  "summary": "A comprehensive guide to building a production-quality blog scraper.",
  "content_markdown": "# How to Build a Blog Scraper\n\nBuilding a blog scraper...\n\n## Getting Started\n\n...",
  "content_text": "How to Build a Blog Scraper Building a blog scraper is a fascinating challenge...",
  "content_blocks": [
    {"type": "heading", "level": 1, "text": "How to Build a Blog Scraper"},
    {"type": "paragraph", "text": "Building a blog scraper is a fascinating challenge..."},
    {"type": "heading", "level": 2, "text": "Getting Started"},
    {"type": "code", "language": "python", "text": "import scrapy\n\nclass BlogSpider(scrapy.Spider):\n    ..."}
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
  "article_score": 72,
  "scraped_at": "2024-02-24T12:00:00+00:00",
  "raw_metadata": {
    "jsonld": {"@type": "Article", "headline": "How to Build a Blog Scraper"},
    "og": {"og:title": "How to Build a Blog Scraper", "og:type": "article"},
    "twitter": {}
  }
}
```

---

## Markdown Output Format

```markdown
# How to Build a Blog Scraper

**Author:** Jane Smith
**Published:** 2024-01-15T10:00:00+00:00
**Tags:** python, web-scraping, scrapy

> A comprehensive guide to building a production-quality blog scraper.

---

Building a blog scraper is a fascinating challenge...

## Getting Started

```python
import scrapy

class BlogSpider(scrapy.Spider):
    ...
```

```

---

## Index JSON (`out/index.json`)

Array of summary objects, sorted by `published_at` descending (unparseable dates last):

```json
[
  {
    "slug": "blog-how-to-build-a-scraper",
    "url": "https://example.com/blog/how-to-build-a-scraper",
    "title": "How to Build a Blog Scraper",
    "author": "Jane Smith",
    "published_at": "2024-01-15T10:00:00+00:00",
    "summary": "A comprehensive guide...",
    "tags": ["python", "web-scraping"],
    "word_count": 842,
    "reading_time_minutes": 5,
    "extraction_method_used": "readability"
  }
]
```

---

## Skipped URLs Log (`out/skipped.jsonl`)

One JSON object per line:

```jsonl
{"url": "https://example.com/tag/python", "reason": "low_article_score (12)", "timestamp": "2024-02-24T12:00:00+00:00"}
{"url": "https://example.com/page/2", "reason": "excluded_pattern (/page/)", "timestamp": "2024-02-24T12:01:00+00:00"}
```
