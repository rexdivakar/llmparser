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

import contextlib
import gzip
import logging
import os
import random
import time
import urllib.error
import urllib.request
import zlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from llmparser.auth import AuthSession
from llmparser.extractors.block_detection import detect_block
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
from llmparser.language import detect_language
from llmparser.playwright_pool import get_playwright_pool
from llmparser.proxy import ProxyConfig, ProxyRotator
from llmparser.rate_limit import DomainRateLimiter

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

    def __init__(
        self,
        message: str,
        url: str = "",
        status: int = 0,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body


def _decode_response_body(
    raw: bytes,
    headers: object | None,
    url: str,
    *,
    allow_brotli: bool = False,
) -> str:
    encoding = ""
    if headers is not None:
        try:
            encoding = str(headers.get("Content-Encoding", "")).lower().strip()
        except Exception:
            encoding = ""

    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding in ("deflate", "zlib"):
        raw = zlib.decompress(raw)
    elif encoding == "br":
        if allow_brotli:
            return ""
        raise FetchError(
            f"Brotli-encoded response from {url} — install 'brotli' or "
            "use render_js=True to let Playwright handle it",
            url=url,
        )

    charset = "utf-8"
    if headers is not None:
        try:
            charset = headers.get_content_charset("utf-8") or "utf-8"
        except Exception:
            charset = "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return raw.decode("utf-8", errors="replace")


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
    proxy: str | None = None,
    auth: AuthSession | None = None,
    rate_limiter: DomainRateLimiter | None = None,
) -> str:
    """Fetch *url* and return the response body as a decoded string.

    Retries up to *max_retries* times with jittered exponential backoff on
    transient errors (429, 500, 502, 503, 504, and network-level failures).

    Args:
        url:         Fully-qualified HTTP/HTTPS URL.
        timeout:     Request timeout in seconds (default 30).
        user_agent:  Override the default browser User-Agent string.
        max_retries: Maximum number of retry attempts (default 3).
        proxy:       Optional proxy URL (e.g. ``"http://host:port"`` or
                     ``"http://user:pass@host:port"``).

    Returns:
        Response body decoded to ``str``.

    Raises:
        FetchError: On HTTP errors, connection failures, or invalid URLs.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Unsupported URL scheme: {parsed.scheme!r}", url=url)

    if rate_limiter:
        rate_limiter.wait(url)

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
    if auth:
        auth.apply_headers(url, req.headers)

    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        _open = urllib.request.build_opener(proxy_handler).open
    else:
        _open = urllib.request.urlopen

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with _open(req, timeout=timeout) as resp:
                raw: bytes = resp.read()
                try:
                    return _decode_response_body(raw, resp.headers, url, allow_brotli=False)
                except OSError as exc:
                    raise FetchError(
                        f"gzip decompression failed for {url}: {exc}", url=url,
                    ) from exc
                except zlib.error as exc:
                    raise FetchError(
                        f"deflate decompression failed for {url}: {exc}", url=url,
                    ) from exc

        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                raw = exc.read()
                if raw:
                    body_text = _decode_response_body(
                        raw, exc.headers, url, allow_brotli=True,
                    )
            except Exception:
                body_text = ""
            if exc.code == 401 and auth and auth.refresh:
                auth.refresh_if_needed()
                auth.apply_headers(url, req.headers)
                if attempt < max_retries:
                    continue
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
                    body=body_text,
                )
                continue
            raise FetchError(
                f"HTTP {exc.code} fetching {url}: {exc.reason}",
                url=url,
                status=exc.code,
                body=body_text,
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


def _fetch_html_playwright(
    url: str,
    timeout: int = 30,
    proxy: str | None = None,
    user_agent: str | None = None,
    auth: AuthSession | None = None,
    page_methods: list[dict] | None = None,
    rate_limiter: DomainRateLimiter | None = None,
) -> str:
    """Fetch *url* via a headless Chromium browser (requires playwright).

    Args:
        url:     Fully-qualified HTTP/HTTPS URL.
        timeout: Request timeout in seconds (Playwright gets max(timeout, 60)).
        proxy:        Optional proxy URL forwarded to Playwright's browser context.
        user_agent:   Optional user-agent string for the browser context.
        auth:         Optional AuthSession to apply headers/cookies.
        page_methods: Optional list of page method calls (JS interactions).
        rate_limiter: Optional per-domain rate limiter.
    """
    if rate_limiter:
        rate_limiter.wait(url)

    try:
        ua = user_agent or _DEFAULT_UA
        base_headers = {"Accept-Language": "en-US,en;q=0.9"}
        if auth:
            auth.apply_headers(url, base_headers)

        use_pool = os.getenv("LLMPARSER_PW_POOL", "1") != "0"
        ctx = None
        browser = None
        if use_pool:
            pool = get_playwright_pool()
            key = (ua, proxy, tuple(sorted(base_headers.items())))
            ctx_kwargs = {
                "user_agent": ua,
                "java_script_enabled": True,
                "viewport": {"width": 1920, "height": 1080},
                "extra_http_headers": base_headers,
            }
            if proxy:
                ctx_kwargs["proxy"] = {"server": proxy}
            ctx = pool.get_context(
                key,
                **ctx_kwargs,
            )
        else:
            from playwright.sync_api import sync_playwright

            p = sync_playwright().start()
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            ctx_kwargs = {
                "user_agent": ua,
                "java_script_enabled": True,
                "viewport": {"width": 1920, "height": 1080},
                "extra_http_headers": base_headers,
            }
            if proxy:
                ctx_kwargs["proxy"] = {"server": proxy}
            ctx = browser.new_context(**ctx_kwargs)

        if auth:
            cookies = auth.playwright_cookies(url)
            if cookies:
                ctx.add_cookies(cookies)

        page = ctx.new_page()
        try:
            effective_timeout = max(timeout, 60) * 1_000

            # Phase 1: wait for "load" (HTML + synchronous scripts ready)
            try:
                page.goto(url, timeout=effective_timeout, wait_until="load")
            except Exception:
                logger.warning("Playwright 'load' timed out for %s — continuing", url)

            # Phase 2: short networkidle wait so SPAs (Angular, React, Vue)
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
                logger.debug("Playwright networkidle reached for %s", url)
            except Exception:
                logger.debug("Playwright networkidle timed out for %s — continuing", url)

            # Phase 3: wait for the DOM to actually contain meaningful text.
            try:
                page.wait_for_function(
                    "() => document.body.innerText.trim()"
                    ".split(/\\s+/).filter(Boolean).length > 50",
                    timeout=12_000,
                )
                logger.debug("Playwright DOM hydration confirmed for %s", url)
            except Exception:
                logger.debug(
                    "Playwright DOM hydration wait timed out for %s — grabbing partial content",
                    url,
                )

            # Phase 4: expand accordion / collapsible sections
            try:
                expanded: int = page.evaluate("""() => {
                    let count = 0;
                    document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                        try { el.click(); count++; } catch (e) {}
                    });
                    document.querySelectorAll('details:not([open])').forEach(el => {
                        el.setAttribute('open', '');
                        count++;
                    });
                    document.querySelectorAll(
                        'mat-expansion-panel:not(.mat-expanded), ' +
                        '.mat-expansion-panel:not(.mat-expanded)'
                    ).forEach(el => {
                        const header = el.querySelector(
                            'mat-expansion-panel-header, ' +
                            '.mat-expansion-panel-header'
                        );
                        if (header) { try { header.click(); count++; } catch(e) {} }
                    });
                    document.querySelectorAll(
                        '.collapse:not(.show), ' +
                        '[data-bs-toggle="collapse"], ' +
                        '[data-toggle="collapse"]'
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
                    try:
                        page.wait_for_load_state("networkidle", timeout=6_000)
                    except Exception:
                        page.wait_for_timeout(1_500)
            except Exception as exc:
                logger.debug("Playwright accordion expansion failed for %s: %s", url, exc)

            if page_methods:
                for entry in page_methods:
                    method = entry.get("method")
                    args = entry.get("args", [])
                    if not method:
                        continue
                    try:
                        getattr(page, method)(*args)
                    except Exception as exc:
                        logger.debug("Playwright method %s failed for %s: %s", method, url, exc)

            html: str = page.content()
            if not html.strip():
                raise FetchError(f"Playwright returned empty page for {url}", url=url)
            return html
        finally:
            with contextlib.suppress(Exception):
                page.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            if "p" in locals():
                with contextlib.suppress(Exception):
                    p.stop()
    except ImportError as exc:
        raise FetchError(
            "render_js=True requires playwright: pip install playwright && "
            "playwright install chromium",
            url=url,
        ) from exc
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

    # Language fallback
    language = meta.get("language")
    if not language and content_text:
        language = detect_language(content_text)

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

    article_score = _heuristics.article_score(url, html)
    block = detect_block(html, url=url)

    return ArticleSchema(
        url=url,
        canonical_url=meta.get("canonical_url") or url or None,
        title=title,
        author=meta.get("author"),
        published_at=meta.get("published_at"),
        updated_at=meta.get("updated_at"),
        site_name=meta.get("site_name"),
        language=language,
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
        article_score=article_score,
        scraped_at=datetime.now(UTC).isoformat(),
        raw_metadata=meta.get("raw_metadata") or {},
        fetch_strategy=fetch_strategy,
        page_type=page_type,
        is_blocked=block.is_blocked,
        block_type=block.block_type,
        block_reason=block.block_reason,
        confidence_score=max(0.0, min(1.0, article_score / 80.0)),
        is_empty=word_count < 20,
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
    proxy_list: list[str] | None = None,
    retry_on_block: bool = True,
    auth: AuthSession | None = None,
    rate_limit_per_domain: float | None = None,
    playwright_page_methods: list[dict] | None = None,
    rate_limiter: DomainRateLimiter | None = None,
) -> ArticleSchema:
    """Fetch *url* and return a fully-extracted :class:`~llmparser.items.ArticleSchema`.

    This is the primary one-call API.  It fetches the page and runs the
    complete extraction pipeline (metadata, readability, trafilatura, DOM
    heuristic, markdown conversion, block parsing).

    Args:
        url:            Fully-qualified HTTP/HTTPS URL to scrape.
        render_js:      If ``True``, render the page with a headless Chromium
                        browser via Playwright before extraction.  Requires
                        ``playwright install chromium``.
        timeout:        Network timeout in seconds (default 30).
        user_agent:     Custom User-Agent string.  Defaults to a realistic
                        Chrome browser UA.
        proxy_list:     Optional list of proxy URLs to rotate through.  When a
                        block is detected and *retry_on_block* is ``True``,
                        the next proxy is selected and the request is retried.
        retry_on_block: If ``True`` (default), retry with a new proxy when a
                        block is detected.  Requires *proxy_list* to be set.
        auth:           Optional AuthSession providing headers/cookies and
                        optional token refresh for 401 responses.
        rate_limit_per_domain: Optional per-domain rate limit (requests/sec).
        playwright_page_methods: Optional list of Playwright page method calls
                        to run after render (JS interactions).
        rate_limiter:  Optional shared DomainRateLimiter instance.

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

    from llmparser.extractors.adaptive import adaptive_fetch_html

    rotator: ProxyRotator | None = None
    if proxy_list:
        rotator = ProxyRotator(ProxyConfig(proxies=proxy_list))

    max_block_retries = min(5, len(proxy_list)) if proxy_list else 0

    def _is_blocked_error(exc: FetchError) -> bool:
        try:
            return detect_block(exc.body or "", url=url, status_code=exc.status).is_blocked
        except Exception:
            return exc.status in (401, 403, 407)

    def _do_fetch(proxy: str | None) -> ArticleSchema:
        if render_js:
            html = _fetch_html_playwright(
                url,
                timeout=timeout,
                proxy=proxy,
                user_agent=user_agent,
                auth=auth,
                page_methods=playwright_page_methods,
                rate_limiter=rate_limiter,
            )
            return extract(html, url=url, fetch_strategy="playwright_forced", page_type=None)
        result = adaptive_fetch_html(
            url,
            timeout=timeout,
            user_agent=user_agent,
            proxy=proxy,
            auth=auth,
            rate_limiter=rate_limiter,
            playwright_page_methods=playwright_page_methods,
        )
        article = extract(
            result.html,
            url=url,
            fetch_strategy=result.strategy_used,
            page_type=result.classification.page_type.value,
        )
        sig = result.classification.signals
        article.raw_metadata["_classification"] = {
            "reason":          result.classification.reason,
            "confidence":      result.classification.confidence,
            "frameworks":      sig.frameworks_detected,
            "amp_url":         sig.amp_url,
            "feed_url":        sig.feed_url,
            "body_word_count": sig.body_word_count,
        }
        return article

    if rate_limiter is None and rate_limit_per_domain:
        rate_limiter = DomainRateLimiter(rate_limit_per_domain)
    current_proxy = rotator.get_proxy() if rotator else None
    try:
        article = _do_fetch(current_proxy)
    except FetchError as exc:
        if retry_on_block and rotator and _is_blocked_error(exc):
            for attempt in range(max_block_retries):
                if current_proxy is not None:
                    rotator.mark_failed(current_proxy)
                current_proxy = rotator.rotate()
                if current_proxy is None or not rotator.has_proxies():
                    logger.warning(
                        "fetch: all proxies exhausted after HTTP block for %s (status=%s)",
                        url, exc.status,
                    )
                    break
                logger.info(
                    "fetch: HTTP block (%s) for %s — retrying with proxy %s (attempt %d/%d)",
                    exc.status, url, current_proxy, attempt + 1, max_block_retries,
                )
                try:
                    article = _do_fetch(current_proxy)
                except FetchError as next_exc:
                    if _is_blocked_error(next_exc):
                        continue
                    raise
                if not article.is_blocked:
                    rotator.mark_success(current_proxy)
                    return article
            raise
        raise

    if retry_on_block and rotator and article.is_blocked:
        for attempt in range(max_block_retries):
            if current_proxy is not None:
                rotator.mark_failed(current_proxy)
            current_proxy = rotator.rotate()
            if current_proxy is None or not rotator.has_proxies():
                logger.warning(
                    "fetch: all proxies exhausted after block for %s (block_type=%s)",
                    url, article.block_type,
                )
                break
            logger.info(
                "fetch: block detected (%s) for %s — retrying with proxy %s (attempt %d/%d)",
                article.block_type, url, current_proxy, attempt + 1, max_block_retries,
            )
            article = _do_fetch(current_proxy)
            if not article.is_blocked:
                logger.info("fetch: block resolved with proxy %s for %s", current_proxy, url)
                rotator.mark_success(current_proxy)
                break

    if rotator and current_proxy and not article.is_blocked:
        rotator.mark_success(current_proxy)

    return article


def parse(html: str, url: str = "") -> ArticleSchema:
    """Parse pre-fetched HTML with no network requests.

    Args:
        html: Raw HTML string to extract content from.
        url:  Original URL of the page (used for link/image resolution and
              extraction hints).  Empty string if unknown.

    Returns:
        :class:`~llmparser.items.ArticleSchema` with all available fields
        populated.  Block detection is run on the supplied HTML.

    Example::

        from llmparser import parse

        article = parse('<html><body>Just a moment...</body></html>',
                        url="https://example.com")
        print(article.is_blocked, article.block_type)
    """
    return extract(html, url=url, fetch_strategy="pre_fetched", page_type=None)


# ---------------------------------------------------------------------------
# Feed API
# ---------------------------------------------------------------------------

def fetch_feed(
    feed_url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
    max_articles: int = 50,
    auth: AuthSession | None = None,
    rate_limit_per_domain: float | None = None,
    playwright_page_methods: list[dict] | None = None,
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
        auth:         Optional AuthSession for headers/cookies/token refresh.
        rate_limit_per_domain: Optional per-domain rate limit (requests/sec).
        playwright_page_methods: Optional Playwright page method calls.

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
    xml_text = fetch_html(
        feed_url,
        timeout=timeout,
        user_agent=user_agent,
        auth=auth,
        rate_limiter=DomainRateLimiter(rate_limit_per_domain)
        if rate_limit_per_domain
        else None,
    )
    entries = parse_feed(xml_text, base_url=feed_url)

    if not entries:
        logger.warning("fetch_feed: no entries found in feed %s", feed_url)
        return []

    logger.info("fetch_feed: %d entries in %s", len(entries), feed_url)
    urls = [e.url for e in entries[:max_articles]]
    return [
        a for a in fetch_batch(
            urls,
            timeout=timeout,
            user_agent=user_agent,
            on_error="skip",
            auth=auth,
            rate_limit_per_domain=rate_limit_per_domain,
            playwright_page_methods=playwright_page_methods,
        )
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
    auth: AuthSession | None = None,
    rate_limit_per_domain: float | None = None,
    playwright_page_methods: list[dict] | None = None,
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
        auth:        Optional AuthSession for headers/cookies/token refresh.
        rate_limit_per_domain: Optional per-domain rate limit (requests/sec).
        playwright_page_methods: Optional Playwright page method calls.

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
    rate_limiter = (
        DomainRateLimiter(rate_limit_per_domain) if rate_limit_per_domain else None
    )

    def _fetch_one(idx: int, url: str) -> tuple[int, ArticleSchema | None]:
        try:
            return idx, fetch(
                url,
                timeout=timeout,
                user_agent=user_agent,
                auth=auth,
                rate_limiter=rate_limiter,
                playwright_page_methods=playwright_page_methods,
            )
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
