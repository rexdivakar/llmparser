"""llmparser.parser — High-level LLMParser class.

Provides a stateful entry point that bundles proxy configuration, block-aware
retry, and both fetch/parse workflows into a single reusable object.

Usage::

    from llmparser import LLMParser

    # Simple fetch (no proxy)
    parser = LLMParser()
    article = parser.fetch("https://example.com/blog/post")

    # With proxy rotation and block-aware retry
    parser = LLMParser(
        proxy_list=["http://p1:8080", "http://p2:8080"],
        retry_on_block=True,
    )
    article = parser.fetch("https://example.com/blog/post")
    print(article.is_blocked, article.confidence_score)

    # Parse pre-fetched HTML (no network)
    article = parser.parse("<html><body>Content...</body></html>",
                           url="https://example.com")

    # Parse from a live Playwright page object
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("https://example.com/blog/post")
        article = parser.parse_from_browser(page)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from llmparser.query import fetch as _fetch
from llmparser.query import parse as _parse

if TYPE_CHECKING:
    from llmparser.items import ArticleSchema


class LLMParser:
    """High-level parser with proxy rotation and block-aware retry.

    All parameters are optional — creating ``LLMParser()`` with no arguments
    gives identical behaviour to calling :func:`llmparser.fetch` directly.

    Args:
        proxy_list:      List of proxy URLs to rotate through.  Supports
                         plain ``http://host:port`` and authenticated
                         ``http://user:pass@host:port`` forms.
        proxy_rotation:  Rotation strategy: ``"round_robin"`` (default) or
                         ``"random"``.  Only used when *proxy_list* is set.
        retry_on_block:  If ``True`` (default), automatically retry with a
                         new proxy when a bot-protection block is detected.
                         Requires *proxy_list* to be non-empty.
        timeout:         Per-request network timeout in seconds (default 30).
        render_js:       If ``True``, use Playwright for all ``fetch()`` calls
                         regardless of the page type.  Requires
                         ``playwright install chromium``.
    """

    def __init__(
        self,
        proxy_list: list[str] | None = None,
        proxy_rotation: str = "round_robin",
        retry_on_block: bool = True,
        timeout: int = 30,
        render_js: bool = False,
    ) -> None:
        self._proxy_list = proxy_list
        self._proxy_rotation = proxy_rotation
        self._retry_on_block = retry_on_block
        self._timeout = timeout
        self._render_js = render_js

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def fetch(self, url: str, **kwargs: Any) -> ArticleSchema:
        """Fetch *url* with configured proxy rotation and block-aware retry.

        All keyword arguments are forwarded to :func:`llmparser.query.fetch`,
        but ``proxy_list``, ``retry_on_block``, ``timeout``, and ``render_js``
        are pre-populated from the constructor unless overridden here.

        Args:
            url:     Fully-qualified HTTP/HTTPS URL to scrape.
            **kwargs: Extra keyword arguments forwarded to
                      :func:`~llmparser.query.fetch` (e.g. ``user_agent``).

        Returns:
            :class:`~llmparser.items.ArticleSchema` instance.

        Raises:
            :class:`~llmparser.query.FetchError`: On unrecoverable fetch
                errors after all retries/proxy rotations are exhausted.
        """
        kwargs.setdefault("proxy_list", self._proxy_list)
        kwargs.setdefault("retry_on_block", self._retry_on_block)
        kwargs.setdefault("timeout", self._timeout)
        kwargs.setdefault("render_js", self._render_js)
        return _fetch(url, **kwargs)

    def parse(self, html: str, url: str = "") -> ArticleSchema:
        """Parse pre-fetched HTML — no network calls.

        Args:
            html: Raw HTML string to extract content from.
            url:  Original URL of the page (used for link/image resolution
                  and extraction hints).  Pass an empty string if unknown.

        Returns:
            :class:`~llmparser.items.ArticleSchema` with all available fields
            populated, including block detection results.
        """
        return _parse(html, url=url)

    def parse_from_browser(self, page: Any) -> ArticleSchema:
        """Parse from a live Playwright ``Page`` object.

        Calls ``page.content()`` and ``page.url`` to obtain the rendered HTML
        and URL, then delegates to :meth:`parse`.  No additional network
        requests are made.

        Args:
            page: A ``playwright.sync_api.Page`` (or async equivalent) with
                  ``content()`` and ``url`` attributes.

        Returns:
            :class:`~llmparser.items.ArticleSchema` parsed from the current
            page state.
        """
        html: str = page.content()
        url: str = page.url
        return self.parse(html, url=url)
