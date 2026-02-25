"""Generic blog spider: crawls one domain, extracts article content.

URL discovery priority:
    1. sitemap.xml / sitemap index
    2. BFS link crawl from start_url

Extraction cascade:
    readability-lxml → trafilatura → DOM heuristic

JS rendering:
    Triggered automatically via needs_js() heuristic (render-js=auto)
    or forced (render-js=always) or disabled (render-js=never).
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.http import Request, Response

from blog_scraper.extractors.heuristics import ARTICLE_SCORE_THRESHOLD, Heuristics
from blog_scraper.extractors.main_content import (
    ExtractionResult,
    extract_images,
    extract_links,
    extract_main_content,
)
from blog_scraper.extractors.markdown import html_to_markdown
from blog_scraper.extractors.metadata import extract_metadata
from blog_scraper.extractors.blocks import html_to_blocks
from blog_scraper.extractors.urlnorm import (
    is_non_content_url,
    normalize_url,
)
from blog_scraper.items import ArticleItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL filtering constants
# ---------------------------------------------------------------------------

# Hard-exclude: purely technical paths that NEVER contain articles
# and should not even be crawled for link discovery.
_HARD_EXCLUDE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"/_next/static/",
        r"/cdn-cgi/",
        r"/wp-content/uploads/",
        r"/__webpack",
        r"/wp-json/",
        r"/wp-admin/",
        r"/xmlrpc\.php",
        r"\.amp(\?|$)",
    ]
)

# Soft-exclude patterns live in blog_scraper.extractors.heuristics and are
# applied during article scoring (not during link discovery).


_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Playwright page methods – wait for full JS render
_PLAYWRIGHT_PAGE_METHODS: list[dict] = [
    {"method": "wait_for_load_state", "args": ["networkidle"]},
]


class BlogSpider(scrapy.Spider):
    """Generic spider that crawls a single blog domain.

    Spider arguments (passed via CLI or process.crawl()):
        start_url      : Entry URL (required)
        max_pages      : Maximum pages to scrape (default 500)
        max_depth      : Maximum BFS depth (default 10)
        render_js      : "auto" | "always" | "never" (default "auto")
        include_regex  : Only follow URLs matching this pattern
        exclude_regex  : Skip URLs matching this pattern
        out_dir        : Override output directory
    """

    name = "blog_spider"

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(
        self,
        start_url: str,
        max_pages: int = 500,
        max_depth: int = 10,
        render_js: str = "auto",
        include_regex: str | None = None,
        exclude_regex: str | None = None,
        out_dir: str = "./out",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.start_url = start_url.strip()
        self.max_pages = int(max_pages)
        self.max_depth = int(max_depth)
        self.render_js = render_js
        self.out_dir = out_dir

        self._include_re = re.compile(include_regex) if include_regex else None
        self._exclude_re = re.compile(exclude_regex) if exclude_regex else None

        parsed = urlparse(self.start_url)
        self.allowed_domain = parsed.netloc.lower()
        self.allowed_domains = [self.allowed_domain]

        self._seen_urls: set[str] = set()
        self._playwright_attempted: set[str] = set()
        self._pages_crawled = 0
        self._skipped: list[dict] = []

        self._heuristics = Heuristics()

    # ------------------------------------------------------------------
    # Start requests
    # ------------------------------------------------------------------

    def start_requests(self) -> Iterator[Request]:
        # Attempt sitemap discovery first
        parsed = urlparse(self.start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        for sitemap_path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]:
            sitemap_url = base + sitemap_path
            yield Request(
                sitemap_url,
                callback=self.parse_sitemap,
                errback=self._sitemap_errback,
                meta={"sitemap_url": sitemap_url},
                priority=10,
                dont_filter=True,
            )

        # Always also crawl the start URL directly
        norm = normalize_url(self.start_url)
        self._seen_urls.add(norm)
        yield self._make_request(
            self.start_url,
            callback=self.parse,
            meta={"depth": 0},
            priority=5,
        )

    # ------------------------------------------------------------------
    # Sitemap parsing
    # ------------------------------------------------------------------

    def parse_sitemap(self, response: Response) -> Iterator[Request]:
        if response.status != 200:
            return

        body = response.text

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            logger.debug("Sitemap at %s is not valid XML", response.url)
            return

        # Strip namespace for easier querying
        tag = root.tag.lower()

        if "sitemapindex" in tag:
            # Sitemap index → recurse into child sitemaps
            for loc_el in root.iter(f"{{{_SITEMAP_NS}}}loc"):
                child_url = (loc_el.text or "").strip()
                if child_url:
                    yield Request(
                        child_url,
                        callback=self.parse_sitemap,
                        errback=self._sitemap_errback,
                        priority=8,
                        dont_filter=True,
                    )
        else:
            # Regular sitemap → extract <url><loc>
            for loc_el in root.iter(f"{{{_SITEMAP_NS}}}loc"):
                url = (loc_el.text or "").strip()
                if not url:
                    continue
                norm = normalize_url(url)
                if norm in self._seen_urls:
                    continue
                if not self._should_crawl(url):
                    continue
                if self._pages_crawled >= self.max_pages:
                    return
                self._seen_urls.add(norm)
                self._pages_crawled += 1
                yield self._make_request(url, callback=self.parse, meta={"depth": 0})

    def _sitemap_errback(self, failure: object) -> None:
        logger.debug("Sitemap fetch failed (expected if no sitemap): %s", failure)

    # ------------------------------------------------------------------
    # Main parse callback
    # ------------------------------------------------------------------

    def parse(self, response: Response) -> Iterator[Request | ArticleItem]:
        url = response.url
        html = response.text
        depth = response.meta.get("depth", 0)
        is_playwright_retry = response.meta.get("playwright_retry", False)
        is_playwright = response.meta.get("playwright", False)

        # Skip non-200 or non-HTML responses
        if response.status != 200:
            self._log_skip(url, f"http_status_{response.status}")
            return

        ct = response.headers.get(b"Content-Type", b"").decode("utf-8", errors="ignore").lower()
        if "html" not in ct and ct:
            self._log_skip(url, f"non_html_content_type ({ct})")
            return

        # JS rendering check (only if not already rendered via Playwright)
        if not is_playwright and not is_playwright_retry and self.render_js != "never":
            needs_render = self.render_js == "always" or self._heuristics.needs_js(html)
            if needs_render and url not in self._playwright_attempted:
                self._playwright_attempted.add(url)
                logger.debug("Triggering Playwright render for %s", url)
                yield self._make_playwright_request(url, depth)
                return

        # Score this page and attempt extraction if it looks article-like
        score = self._heuristics.article_score(url, html)
        logger.debug("Article score=%d for %s", score, url)

        if score >= ARTICLE_SCORE_THRESHOLD and self._should_extract(url):
            item = self._extract_article(url, html, score)
            if item:
                yield item
            else:
                self._log_skip(url, "extraction_returned_empty")
        else:
            reason = (
                "include_regex_mismatch" if not self._should_extract(url)
                else f"low_article_score ({score})"
            )
            self._log_skip(url, reason)

        # Discover and enqueue new links (BFS)
        if depth < self.max_depth:
            yield from self._discover_links(response, html, depth)

    # ------------------------------------------------------------------
    # Article extraction
    # ------------------------------------------------------------------

    def _extract_article(self, url: str, html: str, score: int) -> ArticleItem | None:
        try:
            meta = extract_metadata(html, page_url=url)
        except Exception as exc:
            logger.warning("Metadata extraction failed for %s: %s", url, exc)
            meta = {}

        try:
            result: ExtractionResult = extract_main_content(html, url=url)
        except Exception as exc:
            logger.warning("Content extraction failed for %s: %s", url, exc)
            return None

        if result.word_count < 10:
            logger.debug("Skipping %s – too few words (%d)", url, result.word_count)
            return None

        try:
            content_md = html_to_markdown(result.html)
        except Exception:
            content_md = ""

        try:
            from bs4 import BeautifulSoup
            content_text = BeautifulSoup(result.html, "lxml").get_text(separator=" ")
            content_text = " ".join(content_text.split())
        except Exception:
            content_text = ""

        word_count = len(content_text.split())

        try:
            blocks = html_to_blocks(result.html, base_url=url)
        except Exception:
            blocks = []

        try:
            images = extract_images(result.html, base_url=url)
            # Merge meta images (og:image) at front
            existing_urls = {i["url"] for i in images}
            for img in meta.get("images", []):
                if img["url"] not in existing_urls:
                    images.insert(0, img)
        except Exception:
            images = []

        try:
            links = extract_links(html, base_url=url, base_domain=self.allowed_domain)
        except Exception:
            links = []

        canonical = meta.get("canonical_url") or url
        title = meta.get("title") or self._fallback_title(html)

        item = ArticleItem(
            url=url,
            canonical_url=canonical,
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
            reading_time_minutes=self._heuristics.reading_time(word_count),
            extraction_method_used=result.method,
            article_score=score,
            scraped_at=datetime.now(timezone.utc).isoformat(),
            raw_metadata=meta.get("raw_metadata") or {},
        )
        return item

    @staticmethod
    def _fallback_title(html: str) -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            t = soup.find("title")
            if t:
                return t.get_text().strip()
            h1 = soup.find("h1")
            if h1:
                return h1.get_text().strip()
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Link discovery (BFS)
    # ------------------------------------------------------------------

    def _discover_links(
        self, response: Response, html: str, current_depth: int
    ) -> Iterator[Request]:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return

        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "").strip()
            if not href:
                continue

            try:
                absolute = urljoin(response.url, href)
            except Exception:
                continue

            norm = normalize_url(absolute)
            if norm in self._seen_urls:
                continue

            if not self._should_crawl(absolute):
                continue

            if self._pages_crawled >= self.max_pages:
                return

            self._seen_urls.add(norm)
            self._pages_crawled += 1
            yield self._make_request(
                absolute,
                callback=self.parse,
                meta={"depth": current_depth + 1},
            )

    # ------------------------------------------------------------------
    # URL filtering
    # ------------------------------------------------------------------

    def _should_crawl(self, url: str) -> bool:
        """Return True if *url* should be fetched (for link discovery and/or extraction).

        We keep this check minimal — domain, scheme, and obvious asset extensions.
        Article vs. navigation decisions are made by the scorer inside parse().
        This way tag/category/archive pages are still crawled for their outbound
        links, which may lead to articles not reachable any other way.
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Must be HTTP(S)
        if parsed.scheme not in ("http", "https"):
            return False

        # Same domain only
        if parsed.netloc.lower() != self.allowed_domain:
            return False

        # Skip obvious non-HTML assets
        if is_non_content_url(url):
            return False

        path = parsed.path.lower()

        # Skip internal framework/build paths — never contain articles
        for pat in _HARD_EXCLUDE_PATTERNS:
            if pat.search(path):
                return False

        # User-provided exclude regex
        if self._exclude_re and self._exclude_re.search(url):
            return False

        # User-provided include regex (only restricts extraction, not crawling,
        # so we still crawl non-matching URLs for link discovery)
        return True

    def _should_extract(self, url: str) -> bool:
        """Return True if *url* should be considered for article extraction.

        Applied after scoring — this is a softer filter that respects --include-regex.
        """
        if self._include_re and not self._include_re.search(url):
            return False
        return True

    # ------------------------------------------------------------------
    # Request factories
    # ------------------------------------------------------------------

    def _make_request(
        self,
        url: str,
        callback,
        meta: dict | None = None,
        priority: int = 0,
        **kwargs,
    ) -> Request:
        m = meta or {}
        if self.render_js == "always":
            m["playwright"] = True
            m["playwright_page_methods"] = _PLAYWRIGHT_PAGE_METHODS
        return Request(url, callback=callback, meta=m, priority=priority, **kwargs)

    def _make_playwright_request(self, url: str, depth: int) -> Request:
        return Request(
            url,
            callback=self.parse,
            meta={
                "playwright": True,
                "playwright_retry": True,
                "playwright_page_methods": _PLAYWRIGHT_PAGE_METHODS,
                "depth": depth,
            },
            dont_filter=True,
            priority=3,
        )

    # ------------------------------------------------------------------
    # Skipped URL logging
    # ------------------------------------------------------------------

    def _log_skip(self, url: str, reason: str) -> None:
        self._skipped.append(
            {
                "url": url,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.debug("Skipped %s: %s", url, reason)

    def closed(self, reason: str) -> None:
        """Write skipped URL log and print summary when spider closes."""
        from pathlib import Path

        out = Path(self.out_dir)
        skipped_path = out / "skipped.jsonl"
        try:
            skipped_path.parent.mkdir(parents=True, exist_ok=True)
            with skipped_path.open("a", encoding="utf-8") as f:
                for entry in self._skipped:
                    f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning("Could not write skipped log: %s", exc)

        logger.info(
            "Spider closed (%s): crawled=%d skipped=%d",
            reason,
            self._pages_crawled,
            len(self._skipped),
        )
