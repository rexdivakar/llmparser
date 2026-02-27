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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import defusedxml.ElementTree as defused_ET

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import Request, Response

from llmparser.extractors.blocks import html_to_blocks
from llmparser.extractors.heuristics import ARTICLE_SCORE_THRESHOLD, Heuristics
from llmparser.extractors.main_content import (
    ExtractionResult,
    extract_images,
    extract_links,
    extract_main_content,
)
from llmparser.extractors.markdown import html_to_markdown
from llmparser.extractors.metadata import extract_metadata
from llmparser.extractors.urlnorm import (
    is_non_content_url,
    normalize_url,
)
from llmparser.items import ArticleItem

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

# Soft-exclude patterns live in llmparser.extractors.heuristics and are
# applied during article scoring (not during link discovery).


_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Playwright page methods - wait for full JS render
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
        allow_subdomains: bool = False,      # #3 multi-domain
        extra_domains: str | None = None,    # #3 multi-domain
        resume: bool = False,                # #2 incremental crawl
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.start_url = start_url.strip()
        self.max_pages = int(max_pages)
        self.max_depth = int(max_depth)
        self.render_js = render_js
        self.out_dir = out_dir
        self._allow_subdomains = bool(allow_subdomains)

        self._include_re = re.compile(include_regex) if include_regex else None
        self._exclude_re = re.compile(exclude_regex) if exclude_regex else None

        parsed = urlparse(self.start_url)
        self.allowed_domain = parsed.netloc.lower()

        # #3 - build the full set of explicitly allowed domains
        extra: set[str] = set()
        if extra_domains:
            extra = {d.strip().lower() for d in extra_domains.split(",") if d.strip()}
        self._allowed_domains_set: frozenset[str] = frozenset(
            {self.allowed_domain} | extra,
        )
        # Keep Scrapy's built-in domain filter in sync
        self.allowed_domains = list(self._allowed_domains_set)

        # #2 - incremental resume: load previously-seen URLs from disk
        self._seen_urls_path = (
            (Path(out_dir) / "seen_urls.txt") if resume else None
        )
        self._seen_urls: set[str] = self._load_seen_urls()
        self._seen_urls_handle = (
            self._seen_urls_path.open("a", encoding="utf-8")
            if self._seen_urls_path
            else None
        )

        self._playwright_attempted: set[str] = set()
        self._pages_crawled = 0
        # #9 - no longer accumulate skipped in memory; written directly in _log_skip
        self._skipped_count = 0

        self._heuristics = Heuristics()

        # Clear stale skipped.jsonl from previous runs (unless resuming).
        if not resume:
            skipped_path = Path(out_dir) / "skipped.jsonl"
            try:
                skipped_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not clear skipped.jsonl: %s", exc)

    def _load_seen_urls(self) -> set[str]:
        """Load seen URLs from disk for resume (#2). Returns empty set if not resuming."""
        if not self._seen_urls_path:
            return set()

        urls: set[str] = set()

        # Load incremental seen list from previous run
        if self._seen_urls_path.exists():
            try:
                urls = set(self._seen_urls_path.read_text(encoding="utf-8").splitlines())
                logger.info("Resume: loaded %d URLs from seen_urls.txt", len(urls))
            except OSError as exc:
                logger.warning("Could not read seen_urls.txt: %s", exc)

        # Also load already-extracted article URLs from index.json so they are
        # never re-fetched even across independent crawl sessions (cross-crawl dedup).
        index_path = self._seen_urls_path.parent / "index.json"
        if index_path.exists():
            try:
                entries = json.loads(index_path.read_text(encoding="utf-8"))
                index_urls = {
                    normalize_url(e["url"])
                    for e in entries
                    if isinstance(e, dict) and e.get("url")
                }
                before = len(urls)
                urls |= index_urls
                added = len(urls) - before
                if added:
                    logger.info(
                        "Resume: loaded %d new URLs from index.json (total: %d)",
                        added,
                        len(urls),
                    )
            except Exception as exc:
                logger.warning("Could not read index.json for cross-crawl dedup: %s", exc)

        return urls

    def _mark_seen(self, norm: str) -> None:
        """Add URL to seen set and persist it for resume (#2, #9)."""
        self._seen_urls.add(norm)
        if self._seen_urls_handle:
            try:
                self._seen_urls_handle.write(norm + "\n")
                self._seen_urls_handle.flush()
            except OSError as exc:
                logger.warning("Could not write to seen_urls.txt: %s", exc)

    # ------------------------------------------------------------------
    # Start requests  (Scrapy 2.13+: async generator replaces start_requests)
    # ------------------------------------------------------------------

    def _iter_start_requests(self) -> Iterator[Request]:
        """Shared start request generator for Scrapy 2.11+ compatibility."""
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
        self._mark_seen(norm)
        yield self._make_request(
            self.start_url,
            callback=self.parse,
            meta={"depth": 0},
            priority=5,
        )

    async def start(self) -> AsyncIterator[Request]:  # type: ignore[override]
        for req in self._iter_start_requests():
            yield req

    def start_requests(self) -> Iterator[Request]:
        yield from self._iter_start_requests()

    # ------------------------------------------------------------------
    # Sitemap parsing
    # ------------------------------------------------------------------

    def parse_sitemap(self, response: Response) -> Iterator[Request]:
        if response.status != 200:
            return

        body = response.text

        try:
            root = defused_ET.fromstring(body)
        except defused_ET.ParseError:
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
                self._mark_seen(norm)
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

        ct_bytes: bytes = response.headers.get(b"Content-Type") or b""
        ct = ct_bytes.decode("utf-8", errors="ignore").lower()
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

        # Parse HTML once — shared across scoring, extraction, link discovery (#1)
        try:
            page_soup: BeautifulSoup | None = BeautifulSoup(html, "lxml")
        except Exception as exc:
            logger.warning("BeautifulSoup parse failed for %s: %s", url, exc)
            page_soup = None

        # Score this page and attempt extraction if it looks article-like
        score = self._heuristics.article_score(url, html, soup=page_soup)
        logger.debug("Article score=%d for %s", score, url)

        if score >= ARTICLE_SCORE_THRESHOLD and self._should_extract(url):
            item = self._extract_article(url, html, score, soup=page_soup)
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
            yield from self._discover_links(response, html, depth, soup=page_soup)

    # ------------------------------------------------------------------
    # Article extraction
    # ------------------------------------------------------------------

    def _extract_article(
        self,
        url: str,
        html: str,
        score: int,
        soup: BeautifulSoup | None = None,
    ) -> ArticleItem | None:
        try:
            meta = extract_metadata(html, page_url=url, soup=soup)
        except Exception as exc:
            logger.warning("Metadata extraction failed for %s: %s", url, exc)
            meta = {}

        try:
            result: ExtractionResult = extract_main_content(html, url=url)
        except Exception as exc:
            logger.warning("Content extraction failed for %s: %s", url, exc)
            return None

        if result.word_count < 10:
            logger.debug("Skipping %s - too few words (%d)", url, result.word_count)
            return None

        try:
            content_md = html_to_markdown(result.html)
        except Exception as exc:
            logger.warning("Markdown conversion failed for %s: %s", url, exc)
            content_md = ""

        try:
            # result.html is the extracted article body — separate from the full page soup
            content_text = " ".join(
                BeautifulSoup(result.html, "lxml").get_text(separator=" ").split(),
            )
        except Exception as exc:
            logger.warning("Text extraction failed for %s: %s", url, exc)
            content_text = ""

        word_count = len(content_text.split())

        try:
            blocks = html_to_blocks(result.html, base_url=url)
        except Exception as exc:
            logger.warning("Block extraction failed for %s: %s", url, exc)
            blocks = []

        try:
            images = extract_images(result.html, base_url=url)
            # Merge meta images (og:image) at front
            existing_urls = {i["url"] for i in images}
            for img in meta.get("images", []):
                if img["url"] not in existing_urls:
                    images.insert(0, img)
        except Exception as exc:
            logger.warning("Image extraction failed for %s: %s", url, exc)
            images = []

        try:
            links = extract_links(html, base_url=url, base_domain=self.allowed_domain)
        except Exception as exc:
            logger.warning("Link extraction failed for %s: %s", url, exc)
            links = []

        canonical = meta.get("canonical_url") or url
        title = meta.get("title") or self._fallback_title(html)

        return ArticleItem(
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
            scraped_at=datetime.now(UTC).isoformat(),
            raw_metadata=meta.get("raw_metadata") or {},
        )

    @staticmethod
    def _fallback_title(html: str) -> str:
        try:
            soup = BeautifulSoup(html, "lxml")
            t = soup.find("title")
            if t:
                return t.get_text().strip()
            h1 = soup.find("h1")
            if h1:
                return h1.get_text().strip()
        except Exception as exc:
            logger.debug("Fallback title extraction failed: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Link discovery (BFS)
    # ------------------------------------------------------------------

    def _discover_links(
        self,
        response: Response,
        html: str,
        current_depth: int,
        soup: BeautifulSoup | None = None,
    ) -> Iterator[Request]:
        if soup is None:
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception as exc:
                logger.warning(
                    "BeautifulSoup parse failed for link discovery on %s: %s",
                    response.url,
                    exc,
                )
                return

        # Follow rel="next" pagination links from <head> at higher priority
        # so paginated archives are traversed fully before other BFS links.
        for link_el in soup.find_all("link", rel=True):
            rel_val = link_el.get("rel")
            if isinstance(rel_val, list) and "next" in rel_val:
                href = str(link_el.get("href") or "").strip()
                if not href:
                    continue
                try:
                    absolute = urljoin(response.url, href)
                except Exception as exc:
                    logger.debug("URL join failed for %r: %s", href, exc)
                    continue
                norm = normalize_url(absolute)
                if norm in self._seen_urls:
                    continue
                if not self._should_crawl(absolute):
                    continue
                if self._pages_crawled >= self.max_pages:
                    return
                self._mark_seen(norm)
                self._pages_crawled += 1
                yield self._make_request(
                    absolute,
                    callback=self.parse,
                    meta={"depth": current_depth + 1},
                    priority=5,  # Higher than regular BFS (default 0)
                )

        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "").strip()
            if not href:
                continue

            try:
                absolute = urljoin(response.url, href)
            except Exception as exc:
                logger.debug("URL join failed for %r: %s", href, exc)
                continue

            norm = normalize_url(absolute)
            if norm in self._seen_urls:
                continue

            if not self._should_crawl(absolute):
                continue

            if self._pages_crawled >= self.max_pages:
                return

            self._mark_seen(norm)
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

        # Domain check (#3 multi-domain)
        netloc = parsed.netloc.lower()
        in_explicit = netloc in self._allowed_domains_set
        in_subdomain = self._allow_subdomains and any(
            netloc.endswith("." + d) for d in self._allowed_domains_set
        )
        if not (in_explicit or in_subdomain):
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
        # User-provided include regex (only restricts extraction, not crawling,
        # so we still crawl non-matching URLs for link discovery)
        return not (self._exclude_re and self._exclude_re.search(url))

    def _should_extract(self, url: str) -> bool:
        """Return True if *url* should be considered for article extraction.

        Applied after scoring - this is a softer filter that respects --include-regex.
        """
        return not self._include_re or bool(self._include_re.search(url))

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
        """Write skip entry directly to disk — no in-memory accumulation (#9)."""
        self._skipped_count += 1
        logger.debug("Skipped %s: %s", url, reason)
        skipped_path = Path(self.out_dir) / "skipped.jsonl"
        try:
            skipped_path.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps(
                {
                    "url": url,
                    "reason": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            with skipped_path.open("a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except OSError as exc:
            logger.warning("Could not write skip entry for %s: %s", url, exc)

    def closed(self, reason: str) -> None:
        """Close open file handles and log final stats when spider shuts down."""
        # Close the seen_urls persistence handle (#2)
        if self._seen_urls_handle:
            try:
                self._seen_urls_handle.close()
            except OSError as exc:
                logger.warning("Could not close seen_urls.txt: %s", exc)

        logger.info(
            "Spider closed (%s): crawled=%d skipped=%d",
            reason,
            self._pages_crawled,
            self._skipped_count,
        )
