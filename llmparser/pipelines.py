"""Scrapy item pipelines: dedup → validation → article writer → index writer.

Scrapy 2.14+ compatible: open_spider, close_spider, and process_item do NOT
take a `spider` argument.  Spider identity is not needed at runtime.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from llmparser.extractors.markdown import format_markdown_article
from llmparser.items import ArticleItem, ArticleSchema, article_item_to_schema

if TYPE_CHECKING:
    from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)

_MULTI_DASH_RE = re.compile(r"-{2,}")
_NON_SLUG_RE = re.compile(r"[^\w\-]")


def _slug_from_url(url: str, max_length: int = 100) -> str:
    """Generate a filesystem-safe slug from *url*."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        path = parsed.netloc.replace(".", "-")
    slug = _NON_SLUG_RE.sub("-", path)
    slug = _MULTI_DASH_RE.sub("-", slug).strip("-")
    return (slug[:max_length]).strip("-") or "index"


def _unique_slug(slug: str, seen: set[str]) -> str:
    """Append -2, -3, … until *slug* is not in *seen*."""
    candidate = slug
    counter = 2
    while candidate in seen:
        candidate = f"{slug}-{counter}"
        counter += 1
    seen.add(candidate)
    return candidate


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pipeline 0: content-hash deduplication
# ---------------------------------------------------------------------------

class ContentHashDedupPipeline:
    """Drop articles whose body text matches a previously-seen article.

    Uses a 16-char SHA-256 prefix of the first 5 000 characters of
    ``content_text`` so near-identical pages (syndicated posts, canonical
    mismatches) are only written once per crawl.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        content = (item.get("content_text") or "").strip()
        if len(content) < 100:
            # Too short to hash reliably; let validation decide
            return item

        digest = hashlib.sha256(content[:5_000].encode()).hexdigest()[:16]
        if digest in self._seen:
            from scrapy.exceptions import DropItem  # type: ignore[import-untyped]
            raise DropItem(
                f"Duplicate content (hash={digest}): {item.get('url', '')}"
            )
        self._seen.add(digest)
        return item


# ---------------------------------------------------------------------------
# Pipeline 1: validation
# ---------------------------------------------------------------------------

class ArticleValidationPipeline:
    """Validate items via Pydantic.  Drop items that fail validation or are empty."""

    def __init__(self, skipped_log_path: Path) -> None:
        self.skipped_log_path = skipped_log_path
        self._log_handle: Any = None

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "ArticleValidationPipeline":
        out_dir = Path(crawler.settings.get("OUTPUT_DIR", "./out"))
        return cls(skipped_log_path=out_dir / "skipped.jsonl")

    def open_spider(self) -> None:
        self.skipped_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.skipped_log_path.open("a", encoding="utf-8")
        logger.info("ArticleValidationPipeline open; skipped log: %s", self.skipped_log_path)

    def close_spider(self) -> None:
        if self._log_handle:
            self._log_handle.close()
        logger.info("ArticleValidationPipeline closed")

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item

        url = item.get("url", "")

        # Must have URL and some content
        if not url:
            self._log_skip(url, "missing url")
            raise self._drop_item("missing url")

        content = item.get("content_text", "") or ""
        if len(content.split()) < 10:
            self._log_skip(url, "content too short (<10 words)")
            raise self._drop_item(f"content too short for {url}")

        try:
            article_item_to_schema(item)
        except ValidationError as exc:
            self._log_skip(url, f"validation_error: {exc.error_count()} errors")
            raise self._drop_item(f"validation failed: {exc}") from exc

        return item

    def _log_skip(self, url: str, reason: str) -> None:
        if self._log_handle:
            entry = json.dumps(
                {
                    "url": url,
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._log_handle.write(entry + "\n")
            self._log_handle.flush()

    @staticmethod
    def _drop_item(msg: str) -> Exception:
        from scrapy.exceptions import DropItem  # type: ignore[import-untyped]

        return DropItem(msg)


# ---------------------------------------------------------------------------
# Pipeline 2: article writer
# ---------------------------------------------------------------------------

class ArticleWriterPipeline:
    """Write each article as <slug>.json and <slug>.md."""

    def __init__(self, articles_dir: Path) -> None:
        self.articles_dir = articles_dir
        self._seen_slugs: set[str] = set()
        self._count = 0

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "ArticleWriterPipeline":
        out_dir = Path(crawler.settings.get("OUTPUT_DIR", "./out"))
        return cls(articles_dir=out_dir / "articles")

    def open_spider(self) -> None:
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ArticleWriterPipeline open → %s", self.articles_dir)

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item
        logger.debug("Writing article: %s", item.get("url", ""))

        schema: ArticleSchema = article_item_to_schema(item)
        slug = _unique_slug(_slug_from_url(schema.url), self._seen_slugs)

        # Write JSON
        json_path = self.articles_dir / f"{slug}.json"
        _write_json(json_path, schema.model_dump())

        # Write Markdown
        md_content = format_markdown_article(
            title=schema.title,
            author=schema.author,
            published_at=schema.published_at,
            tags=schema.tags,
            summary=schema.summary,
            content_markdown=schema.content_markdown,
        )
        md_path = self.articles_dir / f"{slug}.md"
        _write_text(md_path, md_content)

        self._count += 1
        logger.info(
            "Wrote article [%d]: %s → %s",
            self._count,
            schema.url,
            slug,
        )

        # Attach slug to item for downstream pipelines
        item["_slug"] = slug
        return item

    def close_spider(self) -> None:
        logger.info("ArticleWriterPipeline: wrote %d articles", self._count)


# ---------------------------------------------------------------------------
# Pipeline 3: index writer
# ---------------------------------------------------------------------------

class IndexWriterPipeline:
    """Stream article summaries to a temp JSONL file; sort and write index.json on close.

    Streaming avoids accumulating all entries in memory for large crawls.
    """

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self._tmp_path = index_path.with_suffix(".jsonl.tmp")
        self._handle: Any = None
        self._count = 0

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "IndexWriterPipeline":
        out_dir = Path(crawler.settings.get("OUTPUT_DIR", "./out"))
        return cls(index_path=out_dir / "index.json")

    def open_spider(self) -> None:
        self._tmp_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._tmp_path.open("w", encoding="utf-8")

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ArticleItem):
            return item
        logger.debug("IndexWriterPipeline received: %s", item.get("url", ""))

        schema: ArticleSchema = article_item_to_schema(item)
        slug = item.get("_slug", _slug_from_url(schema.url))

        entry = {
            "slug": slug,
            "url": schema.url,
            "title": schema.title,
            "author": schema.author,
            "published_at": schema.published_at,
            "summary": schema.summary,
            "tags": schema.tags,
            "word_count": schema.word_count,
            "reading_time_minutes": schema.reading_time_minutes,
            "extraction_method_used": schema.extraction_method_used,
        }
        if self._handle:
            self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._handle.flush()
        self._count += 1
        return item

    def close_spider(self) -> None:
        if self._handle:
            self._handle.close()
            self._handle = None

        # Read back, sort by published_at descending, write final index.json
        entries: list[dict] = []
        if self._tmp_path.exists():
            for line in self._tmp_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
            try:
                self._tmp_path.unlink()
            except Exception:
                pass

        entries.sort(key=lambda e: e.get("published_at") or "", reverse=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.index_path, entries)
        logger.info(
            "IndexWriterPipeline: wrote %d entries to %s",
            len(entries),
            self.index_path,
        )

        # Write CSV index alongside JSON for easy import into spreadsheets / pandas
        csv_path = self.index_path.with_suffix(".csv")
        try:
            buf = io.StringIO()
            if entries:
                writer = csv.DictWriter(buf, fieldnames=list(entries[0].keys()), extrasaction="ignore")
                writer.writeheader()
                writer.writerows(entries)
            csv_path.write_text(buf.getvalue(), encoding="utf-8")
            logger.info("IndexWriterPipeline: wrote CSV index → %s", csv_path)
        except Exception as exc:
            logger.warning("Could not write CSV index: %s", exc)
