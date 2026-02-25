"""Scrapy Items and Pydantic validation schema for extracted articles."""

from __future__ import annotations

from typing import Any, Optional

import scrapy
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Scrapy Item
# ---------------------------------------------------------------------------

class ArticleItem(scrapy.Item):
    """Raw scraped data passed through Scrapy pipelines."""

    # Identity
    url = scrapy.Field()
    canonical_url = scrapy.Field()

    # Metadata
    title = scrapy.Field()
    author = scrapy.Field()
    published_at = scrapy.Field()
    updated_at = scrapy.Field()
    site_name = scrapy.Field()
    language = scrapy.Field()
    tags = scrapy.Field()
    summary = scrapy.Field()

    # Content
    content_markdown = scrapy.Field()
    content_text = scrapy.Field()
    content_blocks = scrapy.Field()

    # Media & links
    images = scrapy.Field()
    links = scrapy.Field()

    # Stats
    word_count = scrapy.Field()
    reading_time_minutes = scrapy.Field()

    # Provenance
    extraction_method_used = scrapy.Field()
    article_score = scrapy.Field()
    scraped_at = scrapy.Field()

    # Raw signals
    raw_metadata = scrapy.Field()

    # Adaptive fetch provenance
    fetch_strategy = scrapy.Field()   # "static" | "amp" | "mobile_ua" | "playwright" | …
    page_type = scrapy.Field()        # "static_html" | "js_spa" | "cookie_walled" | …

    # Pipeline-internal: slug assigned by ArticleWriterPipeline
    _slug = scrapy.Field()


# ---------------------------------------------------------------------------
# Pydantic validation model (used in pipeline for validation + serialization)
# ---------------------------------------------------------------------------

class ImageRef(BaseModel):
    url: str
    alt: str = ""
    caption: str = ""


class LinkRef(BaseModel):
    href: str
    text: str = ""
    rel: str = ""
    is_internal: bool = False


class ContentBlock(BaseModel):
    type: str  # heading|paragraph|image|code|list|quote|table
    # Type-specific fields stored as arbitrary extras
    model_config = {"extra": "allow"}


class RawMetadata(BaseModel):
    jsonld: dict[str, Any] = Field(default_factory=dict)
    og: dict[str, Any] = Field(default_factory=dict)
    twitter: dict[str, Any] = Field(default_factory=dict)


class ArticleSchema(BaseModel):
    """Canonical output schema for a scraped article."""

    # Identity
    url: str
    canonical_url: Optional[str] = None

    # Metadata
    title: str = ""
    author: Optional[str] = None
    published_at: Optional[str] = None
    updated_at: Optional[str] = None
    site_name: Optional[str] = None
    language: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    summary: Optional[str] = None

    # Content
    content_markdown: str = ""
    content_text: str = ""
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)

    # Media & links
    images: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)

    # Stats
    word_count: int = 0
    reading_time_minutes: int = 0

    # Provenance
    extraction_method_used: str = "dom_heuristic"
    article_score: int = 0
    scraped_at: str = ""

    # Raw signals
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    # Adaptive fetch provenance
    fetch_strategy: Optional[str] = None   # which strategy produced the HTML
    page_type: Optional[str] = None        # classified page type

    @field_validator("url", "canonical_url", mode="before")
    @classmethod
    def strip_url(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v or ""


def article_item_to_schema(item: ArticleItem) -> ArticleSchema:
    """Convert a Scrapy ArticleItem dict to a validated ArticleSchema."""
    return ArticleSchema(
        url=item.get("url", ""),
        canonical_url=item.get("canonical_url"),
        title=item.get("title", ""),
        author=item.get("author"),
        published_at=item.get("published_at"),
        updated_at=item.get("updated_at"),
        site_name=item.get("site_name"),
        language=item.get("language"),
        tags=item.get("tags") or [],
        summary=item.get("summary"),
        content_markdown=item.get("content_markdown", ""),
        content_text=item.get("content_text", ""),
        content_blocks=item.get("content_blocks") or [],
        images=item.get("images") or [],
        links=item.get("links") or [],
        word_count=item.get("word_count") or 0,
        reading_time_minutes=item.get("reading_time_minutes") or 0,
        extraction_method_used=item.get("extraction_method_used", "dom_heuristic"),
        article_score=item.get("article_score") or 0,
        scraped_at=item.get("scraped_at", ""),
        raw_metadata=item.get("raw_metadata") or {},
        fetch_strategy=item.get("fetch_strategy"),
        page_type=item.get("page_type"),
    )
