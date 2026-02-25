"""llmparser - extract structured, LLM-ready content from any website.

Quick single-URL usage::

    from llmparser import fetch

    article = fetch("https://example.com/blog/some-post")
    print(article.title)
    print(article.content_markdown)

RAG integration::

    from llmparser import fetch, ArticleChunk, to_jsonl

    article = fetch("https://example.com/blog/some-post")
    chunks  = article.to_chunks(strategy="paragraph", chunk_size=512)
    to_jsonl([article], "/tmp/out.jsonl")

Plugin extension points::

    from llmparser import register_scorer

    class BoostPython:
        name = "boost_python"
        def score(self, url, html, base_score):
            return base_score + (10 if "python" in html.lower() else 0)

    register_scorer(BoostPython())
"""

from llmparser.plugins import (
    register_extractor,
    register_formatter,
    register_scorer,
    register_strategy,
)
from llmparser.query import FetchError, extract, fetch, fetch_batch, fetch_feed, fetch_html
from llmparser.rag import ArticleChunk, to_jsonl

__version__ = "0.1.0"
__all__ = [
    "ArticleChunk",
    "FetchError",
    "extract",
    "fetch",
    "fetch_batch",
    "fetch_feed",
    "fetch_html",
    "register_extractor",
    "register_formatter",
    "register_scorer",
    "register_strategy",
    "to_jsonl",
]
