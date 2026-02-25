"""llmparser.query - single-URL fetch and extraction API.

Lets any Python script import and call ``fetch()`` without running the
full Scrapy crawler.  Uses only the stdlib (``urllib``) for HTTP so no
extra dependencies are required beyond those already in pyproject.toml.

Basic usage::

    from llmparser.query import fetch

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

    from llmparser.query import fetch_html, extract

    html = fetch_html("https://example.com/blog/post")
    article = extract(html, url="https://example.com/blog/post")
"""

from __future__ import annotations

import gzip
import logging
import random
import time
import urllib.error
import urllib.request
import zlib
from datetime import UTC, datetime
from urllib.parse import urlparse

from llmparser.extractors.blocks import html_to_blocks
from llmparser.extractors.feed import parse_feed
from llmparser.extractors.heuristics import Heuristics
from llmparser.extractors.main_content import (
    extract_images,
    extract_links,
    extract_main_content,
)
from llmparser.extractors.markdown import html_to_markdown
from llmparser.extractors.metadata import extract_metadata
from llmparser.items import ArticleSchema

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

_RETRY_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def fetch_html(
    url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
    max_retries: int = 3,
) -> str:
    """Fetch *url* and return the response body as a decoded string.

    Retries up to *max_retries* times with jittered exponential backoff on
    transient errors (429, 500, 502, 503, 504, and network-level failures).

    Args:
        url:         Fully-qualified HTTP/HTTPS URL.
        timeout:     Request timeout in seconds (default 30).
        user_agent:  Override the default browser User-Agent string.
        max_retries: Maximum number of retry attempts (default 3).

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
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        },
    )

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw: bytes = resp.read()
                # urllib does NOT auto-decompress Content-Encoding — do it manually
                encoding = resp.headers.get("Content-Encoding", "").lower().strip()
                if encoding == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError as exc:
                        raise FetchError(
                            f"gzip decompression failed for {url}: {exc}", url=url,
                        ) from exc
                elif encoding in ("deflate", "zlib"):
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error as exc:
                        raise FetchError(
                            f"deflate decompression failed for {url}: {exc}", url=url,
                        ) from exc
                elif encoding == "br":
                    raise FetchError(
                        f"Brotli-encoded response from {url} — install 'brotli' or "
                        "use render_js=True to let Playwright handle it",
                        url=url,
                    )
                # Detect charset from Content-Type, fall back to utf-8
                ct: str = resp.headers.get_content_charset("utf-8") or "utf-8"
                try:
                    return raw.decode(ct, errors="replace")
                except (LookupError, ValueError):
                    return raw.decode("utf-8", errors="replace")

        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_CODES and attempt < max_retries:
                # Honour Retry-After header (RFC 7231 §7.1.3) when present.
                # Servers set this on 429 and some 503 responses.
                retry_after = 0
                try:
                    ra_header = exc.headers.get("Retry-After", "") if exc.headers else ""
                    retry_after = int(ra_header) if ra_header and ra_header.strip().isdigit() else 0
                except Exception:
                    retry_after = 0
                delay = max(retry_after, 2 ** attempt) + random.uniform(0, 1)
                logger.debug(
                    "HTTP %d for %s — retrying in %.1fs (attempt %d/%d)%s",
                    exc.code, url, delay, attempt + 1, max_retries,
                    f" [Retry-After={retry_after}s]" if retry_after else "",
                )
                time.sleep(delay)
                last_exc = FetchError(
                    f"HTTP {exc.code} fetching {url}: {exc.reason}",
                    url=url,
                    status=exc.code,
                )
                continue
            raise FetchError(
                f"HTTP {exc.code} fetching {url}: {exc.reason}",
                url=url,
                status=exc.code,
            ) from exc

        except urllib.error.URLError as exc:
            if attempt < max_retries:
                delay = (2 ** attempt) + random.uniform(0, 1)
                logger.debug(
                    "URL error for %s — retrying in %.1fs (attempt %d/%d): %s",
                    url, delay, attempt + 1, max_retries, exc.reason,
                )
                time.sleep(delay)
                last_exc = FetchError(f"URL error fetching {url}: {exc.reason}", url=url)
                continue
            raise FetchError(f"URL error fetching {url}: {exc.reason}", url=url) from exc

        except OSError as exc:
            if attempt < max_retries:
                delay = (2 ** attempt) + random.uniform(0, 1)
                logger.debug(
                    "Network error for %s — retrying in %.1fs (attempt %d/%d): %s",
                    url, delay, attempt + 1, max_retries, exc,
                )
                time.sleep(delay)
                last_exc = FetchError(f"Network error fetching {url}: {exc}", url=url)
                continue
            raise FetchError(f"Network error fetching {url}: {exc}", url=url) from exc

    # Should only reach here if all retries are exhausted via continue
    raise last_exc or FetchError(f"All retries exhausted for {url}", url=url)


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
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                ctx = browser.new_context(
                    user_agent=_DEFAULT_UA,
                    java_script_enabled=True,
                    viewport={"width": 1920, "height": 1080},
                    # Accept all cookies so cookie-consent walls auto-dismiss
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page = ctx.new_page()
                effective_timeout = max(timeout, 60) * 1_000

                # Phase 1: wait for "load" (HTML + synchronous scripts ready)
                try:
                    page.goto(url, timeout=effective_timeout, wait_until="load")
                except Exception:
                    logger.warning(
                        "Playwright 'load' timed out for %s — continuing", url,
                    )

                # Phase 2: short networkidle wait so SPAs (Angular, React, Vue)
                # can finish their initial XHR/fetch bootstrap calls.
                # We cap at 12 s — many analytics-heavy sites never fully settle.
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                    logger.debug("Playwright networkidle reached for %s", url)
                except Exception:
                    logger.debug(
                        "Playwright networkidle timed out for %s — continuing", url,
                    )

                # Phase 3: wait for the DOM to actually contain meaningful text.
                # This catches SPAs that finish rendering *after* networkidle
                # (e.g. Angular apps that stream data via WebSocket or long-poll).
                try:
                    page.wait_for_function(
                        "() => document.body.innerText.trim()"
                        ".split(/\\s+/).filter(Boolean).length > 50",
                        timeout=12_000,
                    )
                    logger.debug("Playwright DOM hydration confirmed for %s", url)
                except Exception:
                    logger.debug(
                        "Playwright DOM hydration wait timed out for %s — "
                        "grabbing partial content",
                        url,
                    )

                # Phase 4: expand accordion / collapsible sections so their
                # content is present in the DOM before we capture the HTML.
                # Targets: aria-expanded=false, <details>, mat-expansion-panel,
                # and common CSS-hidden expandable containers.
                try:
                    expanded: int = page.evaluate("""() => {
                        let count = 0;

                        // ARIA-based accordions (most frameworks)
                        document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                            try { el.click(); count++; } catch (e) {}
                        });

                        // Native HTML <details> (not yet open)
                        document.querySelectorAll('details:not([open])').forEach(el => {
                            el.setAttribute('open', '');
                            count++;
                        });

                        // Angular Material / CDK expansion panels
                        document.querySelectorAll(
                            'mat-expansion-panel:not(.mat-expanded), ' +
                            '.mat-expansion-panel:not(.mat-expanded)'
                        ).forEach(el => {
                            const header = el.querySelector(
                                'mat-expansion-panel-header, '
                                + '.mat-expansion-panel-header'
                            );
                            if (header) { try { header.click(); count++; } catch(e) {} }
                        });

                        // Bootstrap / generic collapsibles
                        document.querySelectorAll(
                            '.collapse:not(.show), '
                            + '[data-bs-toggle="collapse"], '
                            + '[data-toggle="collapse"]'
                        ).forEach(el => {
                            try { el.click(); count++; } catch (e) {}
                        });

                        return count;
                    }""")
                    if expanded > 0:
                        logger.debug(
                            "Playwright expanded %d accordion sections for %s",
                            expanded, url,
                        )
                        # Wait for any AJAX content triggered by the expansions
                        try:
                            page.wait_for_load_state("networkidle", timeout=6_000)
                        except Exception:
                            page.wait_for_timeout(1_500)
                except Exception as exc:
                    logger.debug("Playwright accordion expansion failed for %s: %s", url, exc)

                html: str = page.content()
                if not html.strip():
                    raise FetchError(
                        f"Playwright returned empty page for {url}", url=url,
                    )
                return html
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"Playwright error fetching {url}: {exc}", url=url) from exc


# ---------------------------------------------------------------------------
# Extraction (pure HTML → ArticleSchema, no network)
# ---------------------------------------------------------------------------

def extract(
    html: str,
    *,
    url: str = "",
    fetch_strategy: str | None = None,
    page_type: str | None = None,
) -> ArticleSchema:
    """Extract article data from *html* and return an :class:`ArticleSchema`.

    This function runs the full extraction pipeline (metadata, main content,
    blocks, markdown) without making any network requests.  Useful when you
    already have the HTML and want structured data.

    Args:
        html: Raw HTML string of the page.
        url:  Original URL of the page (used for canonical resolution,
              relative link/image resolution, and extraction hints).

    Returns:
        :class:`~llmparser.items.ArticleSchema` with all available fields
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
        from llmparser.extractors.main_content import ExtractionResult
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
            BeautifulSoup(result.html, "lxml").get_text(separator=" ").split(),
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
        except Exception as exc:
            logger.debug("Title fallback parse failed: %s", exc)

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
        scraped_at=datetime.now(UTC).isoformat(),
        raw_metadata=meta.get("raw_metadata") or {},
        fetch_strategy=fetch_strategy,
        page_type=page_type,
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
    """Fetch *url* and return a fully-extracted :class:`~llmparser.items.ArticleSchema`.

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
        :class:`~llmparser.items.ArticleSchema` instance.  Access fields
        directly (``article.title``) or call ``.model_dump()`` for a ``dict``.

    Raises:
        :class:`FetchError`: If the URL cannot be fetched (HTTP errors,
            network errors, invalid scheme, Playwright failures).

    Example::

        from llmparser.query import fetch

        article = fetch("https://example.com/blog/post")
        print(article.title)
        print(article.author)
        print(article.word_count)

        # Get everything as a dict
        data = article.model_dump()
    """
    logger.info("fetch: %s (render_js=%s)", url, render_js)

    if render_js:
        # Caller explicitly requested Playwright — skip auto-detection
        html = _fetch_html_playwright(url, timeout=timeout)
        return extract(html, url=url, fetch_strategy="playwright_forced", page_type=None)

    # Adaptive engine: classify page type and select the best strategy
    from llmparser.extractors.adaptive import adaptive_fetch_html

    result = adaptive_fetch_html(url, timeout=timeout, user_agent=user_agent)
    article = extract(
        result.html,
        url=url,
        fetch_strategy=result.strategy_used,
        page_type=result.classification.page_type.value,
    )
    # Embed classification signals in raw_metadata so callers (e.g. evaluate.py)
    # can display analysis without making a second HTTP request.
    sig = result.classification.signals
    article.raw_metadata["_classification"] = {
        "reason":        result.classification.reason,
        "confidence":    result.classification.confidence,
        "frameworks":    sig.frameworks_detected,
        "amp_url":       sig.amp_url,
        "feed_url":      sig.feed_url,
        "body_word_count": sig.body_word_count,
    }
    return article


# ---------------------------------------------------------------------------
# Feed API
# ---------------------------------------------------------------------------

def fetch_feed(
    feed_url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
    max_articles: int = 50,
) -> list[ArticleSchema]:
    """Fetch an RSS/Atom feed and extract each linked article.

    Fetches the feed XML, parses all ``<item>``/``<entry>`` elements, then
    calls :func:`fetch` on each linked URL.  Useful for consuming a site's
    feed directly without full BFS crawling.

    The adaptive engine is used for each article — JS-heavy pages are
    automatically rendered via Playwright when needed.

    Args:
        feed_url:     Fully-qualified RSS or Atom feed URL.
        timeout:      Per-request network timeout in seconds (default 30).
        user_agent:   Custom User-Agent string for all requests.
        max_articles: Maximum number of articles to fetch (default 50).
                      Feed entries beyond this limit are ignored.

    Returns:
        List of :class:`~llmparser.items.ArticleSchema` instances for
        successfully fetched articles.  Failed URLs are silently skipped.

    Raises:
        :class:`FetchError`: If the feed URL itself cannot be fetched.

    Example::

        from llmparser import fetch_feed

        articles = fetch_feed("https://example.com/feed.xml")
        for article in articles:
            print(article.title, article.word_count)
    """
    xml_text = fetch_html(feed_url, timeout=timeout, user_agent=user_agent)
    entries = parse_feed(xml_text, base_url=feed_url)

    if not entries:
        logger.warning("fetch_feed: no entries found in feed %s", feed_url)
        return []

    logger.info("fetch_feed: %d entries in %s", len(entries), feed_url)
    urls = [e.url for e in entries[:max_articles]]
    return [
        a for a in fetch_batch(urls, timeout=timeout, user_agent=user_agent, on_error="skip")
        if a is not None
    ]


# ---------------------------------------------------------------------------
# Batch API
# ---------------------------------------------------------------------------

def fetch_batch(
    urls: list[str],
    *,
    max_workers: int = 8,
    timeout: int = 30,
    user_agent: str | None = None,
    on_error: str = "skip",
) -> list[ArticleSchema | None]:
    """Fetch multiple URLs concurrently and return extracted articles.

    Uses a :class:`~concurrent.futures.ThreadPoolExecutor` to run
    :func:`fetch` in parallel.  Results are returned in the same order as
    *urls* regardless of which requests finish first.

    Args:
        urls:        List of fully-qualified HTTP/HTTPS URLs to scrape.
        max_workers: Maximum number of concurrent fetch threads (default 8).
        timeout:     Per-request network timeout in seconds (default 30).
        user_agent:  Custom User-Agent string for all requests.
        on_error:    How to handle individual URL failures:
                     ``"skip"`` (default) — omit failed URLs from results;
                     ``"raise"`` — re-raise the first exception immediately;
                     ``"include"`` — include ``None`` in results for failures.

    Returns:
        List of :class:`~llmparser.items.ArticleSchema` instances.
        With ``on_error="skip"`` the list may be shorter than *urls*.
        With ``on_error="include"`` the list has exactly ``len(urls)`` entries,
        with ``None`` for URLs that failed.

    Raises:
        :class:`FetchError`: Only when ``on_error="raise"`` and any URL fails.
        :class:`ValueError`: For unknown *on_error* values.

    Example::

        from llmparser import fetch_batch

        articles = fetch_batch([
            "https://example.com/post/1",
            "https://example.com/post/2",
        ], max_workers=4)
        for article in articles:
            print(article.title, article.word_count)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if on_error not in ("skip", "raise", "include"):
        raise ValueError(f"on_error must be 'skip', 'raise', or 'include'; got {on_error!r}")

    # Preserve input order: slot results by original index
    results: list[ArticleSchema | None] = [None] * len(urls)

    def _fetch_one(idx: int, url: str) -> tuple[int, ArticleSchema | None]:
        try:
            return idx, fetch(url, timeout=timeout, user_agent=user_agent)
        except Exception as exc:
            if on_error == "raise":
                raise
            logger.warning("fetch_batch: failed to fetch %s: %s", url, exc)
            return idx, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_one, i, url): i
            for i, url in enumerate(urls)
        }
        for future in as_completed(futures):
            idx, article = future.result()
            results[idx] = article

    if on_error == "include":
        return results
    return [r for r in results if r is not None]
