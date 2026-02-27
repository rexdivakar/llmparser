"""llmparser.rag — Native RAG integration helpers.

Provides chunking utilities and adapters for LangChain, LlamaIndex, and
JSONL export (Pinecone / Chroma / Weaviate / Qdrant compatible).

Usage::

    from llmparser import fetch
    from llmparser.rag import chunk_article, to_jsonl

    article = fetch("https://example.com/blog/post")
    chunks  = chunk_article(article, strategy="paragraph", chunk_size=512)
    to_jsonl([article], "/tmp/out.jsonl")

    # Framework adapters (requires optional extras):
    docs  = article.to_langchain()   # pip install langchain-core
    nodes = article.to_llamaindex()  # pip install llama-index-core
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llmparser.items import ArticleSchema


# ---------------------------------------------------------------------------
# ArticleChunk
# ---------------------------------------------------------------------------

@dataclass
class ArticleChunk:
    """A single chunk of text extracted from an article for RAG ingestion."""

    text: str
    metadata: dict[str, Any]
    chunk_index: int
    article_url: str
    chunk_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        # Ensure chunk_id is set when constructed with explicit values
        if not self.chunk_id:
            self.chunk_id = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug_from_url(url: str) -> str:
    """Generate a short identifier from a URL for use in chunk IDs."""
    slug = re.sub(r"https?://", "", url)
    slug = re.sub(r"[^\w]", "_", slug)
    return slug[:60].strip("_") or "article"


def _build_metadata(
    article: ArticleSchema,
    chunk_index: int,
    chunk_count: int,
    text: str,
) -> dict[str, Any]:
    return {
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "published_at": article.published_at,
        "tags": list(article.tags),
        "site_name": article.site_name,
        "language": article.language,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "word_count": len(text.split()),
    }


# ---------------------------------------------------------------------------
# Splitting strategies
# ---------------------------------------------------------------------------

def _split_paragraph(article: ArticleSchema, chunk_size: int, overlap: int) -> list[str]:
    """Split using content_blocks (heading/paragraph boundaries), merge to chunk_size."""
    blocks = article.content_blocks
    if blocks:
        # Extract text from each block
        segments: list[str] = []
        for block in blocks:
            btype = block.get("type", "")
            if btype == "heading":
                level = block.get("level", 2)
                text = block.get("text", "").strip()
                if text:
                    segments.append(f"{'#' * level} {text}")
            elif btype == "paragraph":
                text = block.get("text", "").strip()
                if text:
                    segments.append(text)
            elif btype == "code":
                text = block.get("text", "").strip()
                lang = block.get("language", "")
                if text:
                    segments.append(f"```{lang}\n{text}\n```")
            elif btype == "list":
                items = block.get("items") or []
                if isinstance(items, list) and items:
                    ordered = bool(block.get("ordered", False))
                    lines = []
                    for i, item in enumerate(items, 1):
                        prefix = f"{i}." if ordered else "-"
                        lines.append(f"{prefix} {item}")
                    segments.append("\n".join(lines))
            elif btype == "quote":
                text = block.get("text", "").strip()
                if text:
                    lines = [f"> {line}" for line in text.splitlines() if line.strip()]
                    segments.append("\n".join(lines))
            elif btype == "table":
                rows = block.get("rows") or []
                if isinstance(rows, list) and rows:
                    header = [str(c) for c in rows[0]]
                    lines = ["| " + " | ".join(header) + " |"]
                    lines.append("| " + " | ".join("---" for _ in header) + " |")
                    for row in rows[1:]:
                        lines.append("| " + " | ".join(str(c) for c in row) + " |")
                    segments.append("\n".join(lines))
            elif btype == "image":
                url = block.get("url", "").strip()
                alt = block.get("alt", "").strip()
                if url:
                    segments.append(f"![{alt}]({url})")
    else:
        # Fall back: split content_text on double newlines
        raw = article.content_text or article.content_markdown or ""
        segments = [s.strip() for s in re.split(r"\n{2,}", raw) if s.strip()]

    if not segments:
        return [article.content_text] if article.content_text else []

    # Merge segments into chunks of up to chunk_size chars with overlap
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for seg in segments:
        seg_len = len(seg)
        if current_len + seg_len + (1 if current_parts else 0) > chunk_size and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Retain overlap: keep last few chars worth of segments
            kept: list[str] = []
            kept_len = 0
            for part in reversed(current_parts):
                if kept_len + len(part) <= overlap:
                    kept.insert(0, part)
                    kept_len += len(part)
                else:
                    break
            current_parts = kept
            current_len = kept_len
        current_parts.append(seg)
        current_len += seg_len

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def _split_fixed(article: ArticleSchema, chunk_size: int, overlap: int) -> list[str]:
    """Sliding-window split of chunk_size chars with overlap."""
    text = article.content_text or article.content_markdown or ""
    if not text:
        return []
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += step
    return chunks


def _split_sentence(article: ArticleSchema, chunk_size: int, overlap: int) -> list[str]:
    """Split on sentence endings, then group into chunks under chunk_size chars."""
    text = article.content_text or article.content_markdown or ""
    if not text:
        return []

    # Split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return [text]

    chunks: list[str] = []
    current_sents: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len + (1 if current_sents else 0) > chunk_size and current_sents:
            chunks.append(" ".join(current_sents))
            # Retain overlap
            kept: list[str] = []
            kept_len = 0
            for s in reversed(current_sents):
                if kept_len + len(s) <= overlap:
                    kept.insert(0, s)
                    kept_len += len(s)
                else:
                    break
            current_sents = kept
            current_len = kept_len
        current_sents.append(sent)
        current_len += sent_len

    if current_sents:
        chunks.append(" ".join(current_sents))

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_article(
    article: ArticleSchema,
    *,
    chunk_size: int = 512,
    overlap: int = 50,
    unit: str = "chars",
    strategy: str = "paragraph",
) -> list[ArticleChunk]:
    """Split *article* into RAG-ready chunks.

    Args:
        article:    A validated ``ArticleSchema`` instance.
        chunk_size: Maximum size of each chunk (in *unit*).
        overlap:    Number of *unit*s to repeat at the start of the next chunk.
        unit:       Always ``"chars"`` (word-based is reserved for a future release).
        strategy:   ``"paragraph"`` | ``"fixed"`` | ``"sentence"``

    Returns:
        A list of :class:`ArticleChunk` objects, one per text segment.
    """
    if unit != "chars":
        raise ValueError(f"unit={unit!r} is not supported; only 'chars' is available.")

    if strategy == "paragraph":
        raw_chunks = _split_paragraph(article, chunk_size, overlap)
    elif strategy == "fixed":
        raw_chunks = _split_fixed(article, chunk_size, overlap)
    elif strategy == "sentence":
        raw_chunks = _split_sentence(article, chunk_size, overlap)
    else:
        raise ValueError(f"Unknown strategy {strategy!r}. Use 'paragraph', 'fixed', or 'sentence'.")

    # Filter empty
    raw_chunks = [c for c in raw_chunks if c.strip()]
    if not raw_chunks:
        return []

    slug = _slug_from_url(article.url)
    chunk_count = len(raw_chunks)
    result: list[ArticleChunk] = []

    for i, text in enumerate(raw_chunks):
        meta = _build_metadata(article, chunk_index=i, chunk_count=chunk_count, text=text)
        chunk_id = f"{slug}_{i}"
        result.append(
            ArticleChunk(
                text=text,
                metadata=meta,
                chunk_index=i,
                article_url=article.url,
                chunk_id=chunk_id,
            ),
        )

    return result


def to_langchain(article: ArticleSchema, **chunk_kwargs: Any) -> list[Any]:
    """Convert *article* to a list of LangChain ``Document`` objects.

    Requires: ``pip install langchain-core``

    Each document's ``page_content`` is the chunk text;
    ``metadata`` carries url, title, author, published_at, tags, etc.
    """
    try:
        from langchain_core.documents import Document  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "langchain-core is required for to_langchain(). "
            "Install it with: pip install langchain-core",
        ) from exc

    chunks = chunk_article(article, **chunk_kwargs)
    return [Document(page_content=chunk.text, metadata=chunk.metadata) for chunk in chunks]


def to_llamaindex(article: ArticleSchema, **chunk_kwargs: Any) -> list[Any]:
    """Convert *article* to a list of LlamaIndex ``TextNode`` objects.

    Requires: ``pip install llama-index-core``

    Each node's ``text`` is the chunk text; ``metadata`` carries article fields;
    ``id_`` is the stable ``chunk_id`` (``<slug>_<index>``).
    """
    try:
        from llama_index.core.schema import TextNode  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "llama-index-core is required for to_llamaindex(). "
            "Install it with: pip install llama-index-core",
        ) from exc

    chunks = chunk_article(article, **chunk_kwargs)
    return [
        TextNode(text=chunk.text, metadata=chunk.metadata, id_=chunk.chunk_id)
        for chunk in chunks
    ]


def to_jsonl(
    articles: list[ArticleSchema],
    path: str | Path,
    **chunk_kwargs: Any,
) -> int:
    """Write all article chunks to a JSONL file compatible with vector DB upsert formats.

    Each line is a JSON object: ``{"id": chunk_id, "text": ..., "metadata": {...}}``.
    Compatible with Pinecone, Chroma, Weaviate, and Qdrant upsert formats.

    Args:
        articles:     List of ``ArticleSchema`` objects to chunk and export.
        path:         Output file path (created/overwritten).
        **chunk_kwargs: Passed through to :func:`chunk_article`.

    Returns:
        Total number of chunks written.
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for article in articles:
            for chunk in chunk_article(article, **chunk_kwargs):
                line = json.dumps(
                    {"id": chunk.chunk_id, "text": chunk.text, "metadata": chunk.metadata},
                    ensure_ascii=False,
                )
                fh.write(line + "\n")
                total += 1

    return total
