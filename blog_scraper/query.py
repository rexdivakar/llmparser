"""blog_scraper.query – single-URL fetch and extraction API.

Lets any Python script import and call ``fetch()`` without running the
full Scrapy crawler.  Uses only the stdlib (``urllib``) for HTTP so no
extra dependencies are required beyond those already in pyproject.toml.

Basic usage::

    from blog_scraper.query import fetch

    article = fetch("https://example.com/blog/some-post")
    print(article.title)
    print(article.author)
    print(article.published_at)
    print(article.content_markdown)
    print(article.word_count)

    # As a plain dict
    data = fetch("https://example.com/blog/some-post").model_dump()

JavaScript-heavy pages::

    article = fetch("https://reactapp.io/blog/post", render_js=True)

Low-level access::

    from blog_scraper.query import fetch_html, extract

    html = fetch_html("https://example.com/blog/post")
    article = extract(html, url="https://example.com/blog/post")
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from blog_scraper.extractors.blocks import html_to_blocks
from blog_scraper.extractors.main_content import (
    extract_images,
    extract_links,
    extract_main_content,
)
from blog_scraper.extractors.markdown import html_to_markdown
from blog_scraper.extractors.metadata import extract_metadata
from blog_scraper.extractors.heuristics import Heuristics
from blog_scraper.items import ArticleSchema

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_heuristics = Heuristics()


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched or parsed.

    Attributes:
        url    -- the URL that failed
        status -- HTTP status code (0 if no response was received)
    """

    def __init__(self, message: str, url: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.url = url
        self.status = status


# ---------------------------------------------------------------------------
# Low-level HTTP fetch
# ---------------------------------------------------------------------------

def fetch_html(
    url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
) -> str:
    """Fetch *url* and return the response body as a decoded string.

    Args:
        url:        Fully-qualified HTTP/HTTPS URL.
        timeout:    Request timeout in seconds (default 30).
        user_agent: Override the default browser User-Agent string.

    Returns:
        Response body decoded to ``str``.

    Raises:
        FetchError: On HTTP errors, connection failures, or invalid URLs.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Unsupported URL scheme: {parsed.scheme!r}", url=url)

    ua = user_agent or _DEFAULT_UA
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw: bytes = resp.read()
            # Detect encoding from Content-Type header, fall back to utf-8
            ct: str = resp.headers.get_content_charset("utf-8") or "utf-8"
            try:
                return raw.decode(ct, errors="replace")
            except (LookupError, ValueError):
                return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(
            f"HTTP {exc.code} fetching {url}: {exc.reason}",
            url=url,
            status=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"URL error fetching {url}: {exc.reason}", url=url) from exc
    except OSError as exc:
        raise FetchError(f"Network error fetching {url}: {exc}", url=url) from exc


def _fetch_html_playwright(url: str, timeout: int = 30) -> str:
    """Fetch *url* via a headless Chromium browser (requires playwright)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError(
            "render_js=True requires playwright: pip install playwright && "
            "playwright install chromium",
            url=url,
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    user_agent=_DEFAULT_UA,
                    java_script_enabled=True,
                )
                page.goto(url, timeout=timeout * 1_000, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"Playwright error fetching {url}: {exc}", url=url) from exc


# ---------------------------------------------------------------------------
# Extraction (pure HTML → ArticleSchema, no network)
# ---------------------------------------------------------------------------

def extract(html: str, *, url: str = "") -> ArticleSchema:
    """Extract article data from *html* and return an :class:`ArticleSchema`.

    This function runs the full extraction pipeline (metadata, main content,
    blocks, markdown) without making any network requests.  Useful when you
    already have the HTML and want structured data.

    Args:
        html: Raw HTML string of the page.
        url:  Original URL of the page (used for canonical resolution,
              relative link/image resolution, and extraction hints).

    Returns:
        :class:`~blog_scraper.items.ArticleSchema` with all available fields
        populated.  Fields that cannot be extracted are ``None`` or empty.
    """
    domain = urlparse(url).netloc.lower() if url else ""

    # Metadata (JSON-LD, OG, Twitter Card, meta tags)
    try:
        meta = extract_metadata(html, page_url=url)
    except Exception as exc:
        logger.warning("metadata extraction failed for %s: %s", url, exc)
        meta = {}

    # Main content (readability → trafilatura → DOM heuristic)
    try:
        result = extract_main_content(html, url=url)
    except Exception as exc:
        logger.warning("content extraction failed for %s: %s", url, exc)
        from blog_scraper.extractors.main_content import ExtractionResult
        result = ExtractionResult(html=html, method="dom_heuristic", word_count=0)

    # Markdown
    try:
        content_md = html_to_markdown(result.html)
    except Exception:
        content_md = ""

    # Plain text
    try:
        from bs4 import BeautifulSoup
        content_text = " ".join(
            BeautifulSoup(result.html, "lxml").get_text(separator=" ").split()
        )
    except Exception:
        content_text = ""

    word_count = len(content_text.split())

    # Structured blocks
    try:
        blocks = html_to_blocks(result.html, base_url=url)
    except Exception:
        blocks = []

    # Images: merge content images + OG image
    try:
        images = extract_images(result.html, base_url=url)
        existing = {i["url"] for i in images}
        for img in meta.get("images", []):
            if img["url"] not in existing:
                images.insert(0, img)
    except Exception:
        images = []

    # Links
    try:
        links = extract_links(html, base_url=url, base_domain=domain)
    except Exception:
        links = []

    # Fallback title from <title> or <h1>
    title = meta.get("title") or ""
    if not title:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            t = soup.find("title")
            if t:
                title = t.get_text().strip()
            if not title:
                h1 = soup.find("h1")
                if h1:
                    title = h1.get_text().strip()
        except Exception:
            pass

    return ArticleSchema(
        url=url,
        canonical_url=meta.get("canonical_url") or url or None,
        title=title,
        author=meta.get("author"),
        published_at=meta.get("published_at"),
        updated_at=meta.get("updated_at"),
        site_name=meta.get("site_name"),
        language=meta.get("language"),
        tags=meta.get("tags") or [],
        summary=meta.get("summary"),
        content_markdown=content_md,
        content_text=content_text,
        content_blocks=blocks,
        images=images,
        links=links,
        word_count=word_count,
        reading_time_minutes=_heuristics.reading_time(word_count),
        extraction_method_used=result.method,
        article_score=_heuristics.article_score(url, html),
        scraped_at=datetime.now(timezone.utc).isoformat(),
        raw_metadata=meta.get("raw_metadata") or {},
    )


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def fetch(
    url: str,
    *,
    render_js: bool = False,
    timeout: int = 30,
    user_agent: str | None = None,
) -> ArticleSchema:
    """Fetch *url* and return a fully-extracted :class:`~blog_scraper.items.ArticleSchema`.

    This is the primary one-call API.  It fetches the page and runs the
    complete extraction pipeline (metadata, readability, trafilatura, DOM
    heuristic, markdown conversion, block parsing).

    Args:
        url:        Fully-qualified HTTP/HTTPS URL to scrape.
        render_js:  If ``True``, render the page with a headless Chromium
                    browser via Playwright before extraction.  Requires
                    ``playwright install chromium``.
        timeout:    Network timeout in seconds (default 30).
        user_agent: Custom User-Agent string.  Defaults to a realistic
                    Chrome browser UA.

    Returns:
        :class:`~blog_scraper.items.ArticleSchema` instance.  Access fields
        directly (``article.title``) or call ``.model_dump()`` for a ``dict``.

    Raises:
        :class:`FetchError`: If the URL cannot be fetched (HTTP errors,
            network errors, invalid scheme, Playwright failures).

    Example::

        from blog_scraper.query import fetch

        article = fetch("https://example.com/blog/post")
        print(article.title)
        print(article.author)
        print(article.word_count)

        # Get everything as a dict
        data = article.model_dump()
    """
    logger.info("fetch: %s (render_js=%s)", url, render_js)

    if render_js:
        html = _fetch_html_playwright(url, timeout=timeout)
    else:
        html = fetch_html(url, timeout=timeout, user_agent=user_agent)

    return extract(html, url=url)
