"""blog_scraper – production-quality blog crawling and extraction package.

Quick single-URL usage::

    from blog_scraper import fetch

    article = fetch("https://example.com/blog/some-post")
    print(article.title)
    print(article.content_markdown)
"""

from blog_scraper.query import FetchError, extract, fetch, fetch_batch, fetch_html

__version__ = "0.1.0"
__all__ = ["fetch", "fetch_batch", "fetch_html", "extract", "FetchError"]
