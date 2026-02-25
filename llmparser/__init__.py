"""llmparser – extract structured, LLM-ready content from any website.

Quick single-URL usage::

    from llmparser import fetch

    article = fetch("https://example.com/blog/some-post")
    print(article.title)
    print(article.content_markdown)
"""

from llmparser.query import FetchError, extract, fetch, fetch_batch, fetch_feed, fetch_html

__version__ = "0.1.0"
__all__ = ["fetch", "fetch_batch", "fetch_feed", "fetch_html", "extract", "FetchError"]
