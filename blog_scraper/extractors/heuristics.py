"""Deterministic heuristics for article scoring and JS rendering detection."""

from __future__ import annotations

import math
import re

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTICLE_SCORE_THRESHOLD = 35

_ARTICLE_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "blog",
        "blogs",
        "post",
        "posts",
        "article",
        "articles",
        "news",
        "story",
        "stories",
        "essay",
        "essays",
        "journal",
        "write",
        "writing",
        "p",
        "entry",
        "entries",
        "publication",
        "publications",
        "insight",
        "insights",
        "tutorial",
        "tutorials",
        "guide",
        "guides",
        "learn",
        "thought",
        "thoughts",
    }
)

_EXCLUDED_PATH_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(pat, re.IGNORECASE)
    for pat in [
        r"/tag/",
        r"/tags/",
        r"/category/",
        r"/categories/",
        r"/search(\?|$|/)",
        r"/login(\?|$|/)",
        r"/signin(\?|$|/)",
        r"/signup(\?|$|/)",
        r"/register(\?|$|/)",
        r"/logout(\?|$|/)",
        r"/privacy(\?|$|/)",
        r"/terms(\?|$|/)",
        r"/feed(\?|$|/)",
        r"/rss(\?|$|/)",
        r"/sitemap",
        r"/archive(\?|$|/)",
        r"/archives(\?|$|/)",
        r"/_next/static/",
        r"/cdn-cgi/",
        r"/wp-content/uploads/",
        r"/__webpack",
        r"/page/\d+",
    ]
)

_DATE_IN_PATH_RE = re.compile(r"/\d{4}/\d{2}(/\d{2})?")

_ARTICLE_JSONLD_TYPES: frozenset[str] = frozenset(
    {
        "article",
        "blogging",
        "blogposting",
        "newsarticle",
        "techarticle",
        "scholarlyarticle",
        "liveblogposting",
        "reportage",
        "satiricalarticle",
        "socialmediaposting",
    }
)

_JS_ROOT_SELECTORS: tuple[str, ...] = (
    "#__next",
    "#app",
    "#root",
    "#__nuxt",
    "#app-root",
    "#gatsby-focus-wrapper",
    "[data-reactroot]",
    "[data-server-rendered]",
    "div[ng-app]",
    "#angular-app",
    "#ember-application",
)

_JS_REQUIRED_PHRASES: tuple[str, ...] = (
    "enable javascript",
    "javascript is required",
    "please enable javascript",
    "javascript must be enabled",
    "this site requires javascript",
    "you need to enable javascript",
    "requires javascript to function",
)

# Selectors to remove before counting words for JS detection
_NOISE_TAGS: tuple[str, ...] = ("script", "style", "nav", "header", "footer", "noscript")


class Heuristics:
    """Stateless heuristic scorer. Safe to instantiate once and reuse."""

    # ------------------------------------------------------------------
    # Article scoring
    # ------------------------------------------------------------------

    def article_score(
        self,
        url: str,
        html: str,
        soup: BeautifulSoup | None = None,
    ) -> int:
        """Return an integer score 0-100+ indicating how likely *url/html*
        is a single article page.  Threshold: ARTICLE_SCORE_THRESHOLD.

        Pass a pre-parsed *soup* to avoid re-parsing the HTML.
        """
        score = 0
        score += self._url_score(url)
        score += self._content_score(html, soup=soup)
        return score

    def _url_score(self, url: str) -> int:
        from urllib.parse import urlparse, parse_qs

        score = 0
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Hard exclude patterns
        for pat in _EXCLUDED_PATH_PATTERNS:
            if pat.search(path):
                return -30  # Immediately penalise; no point checking further

        # Article path segments
        segments = [s for s in path.split("/") if s]
        if any(seg in _ARTICLE_PATH_SEGMENTS for seg in segments):
            score += 15

        # Date in path
        if _DATE_IN_PATH_RE.search(path):
            score += 10

        # Long slug (depth >= 2 means at least /a/b — common for any blog)
        content_segments = len(segments)
        if content_segments >= 4:
            score += 5
        elif content_segments == 2:
            score += 3  # /year/slug or /category/post is fine
        elif content_segments <= 1:
            score -= 20

        # Paginated
        qs = parse_qs(parsed.query)
        if "page" in qs or re.search(r"/page/\d+", path):
            score -= 15

        # Author listing without further path
        if "/author/" in path and len(segments) <= 2:
            score -= 10

        return score

    def _content_score(self, html: str, soup: BeautifulSoup | None = None) -> int:
        """Score based on parsed HTML content.

        Pass a pre-parsed *soup* to skip re-parsing.
        """
        score = 0
        if soup is None:
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                return 0

        # Remove noise elements for word counting
        for tag in ("nav", "header", "footer", "aside", "script", "style", "noscript"):
            for el in soup.find_all(tag):
                el.decompose()

        body_text = soup.get_text(separator=" ")
        words = len(body_text.split())

        if words > 300:
            score += 20
        elif words >= 150:
            score += 10
        elif words < 50:
            score -= 20

        # H1 count
        h1s = soup.find_all("h1")
        if len(h1s) == 1:
            score += 15
        elif len(h1s) > 3:
            score -= 5

        # Substantial paragraphs (3+ paragraphs ≥ 20 words each is already good)
        paras = [p for p in soup.find_all("p") if len(p.get_text().split()) >= 20]
        if len(paras) >= 3:
            score += 5

        # Metadata signals
        metas = self._quick_meta(soup)
        if metas.get("has_author"):
            score += 10
        if metas.get("has_date"):
            score += 10
        if metas.get("jsonld_article"):
            score += 10
        if metas.get("og_article"):
            score += 5

        # Outbound links (listing pages have many)
        links = soup.find_all("a", href=True)
        if len(links) > 30:
            score -= 10

        # Pagination links – rel is a multi-valued list attribute in BS4
        for link_tag in soup.find_all("link"):
            rel_val = link_tag.get("rel")
            if isinstance(rel_val, list) and any(
                v in ("next", "prev") for v in rel_val
            ):
                score -= 15
                break

        return score

    @staticmethod
    def _quick_meta(soup: BeautifulSoup) -> dict:
        """Fast partial metadata scan for scoring (no full extraction)."""
        result: dict = {
            "has_author": False,
            "has_date": False,
            "jsonld_article": False,
            "og_article": False,
        }

        # JSON-LD scan
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json

                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next(iter(data), {})
                dtype = str(data.get("@type", "")).lower()
                if dtype in _ARTICLE_JSONLD_TYPES:
                    result["jsonld_article"] = True
                if data.get("author"):
                    result["has_author"] = True
                if data.get("datePublished"):
                    result["has_date"] = True
            except Exception:
                pass

        # OG / meta tags
        for tag in soup.find_all("meta"):
            prop_raw = tag.get("property") or tag.get("name") or ""
            prop = prop_raw.lower() if isinstance(prop_raw, str) else ""
            content_raw = tag.get("content") or ""
            content = content_raw if isinstance(content_raw, str) else ""
            if prop == "og:type" and content.lower() == "article":
                result["og_article"] = True
            if prop in ("og:type", "article:published_time") and content:
                result["has_date"] = True
            if prop in ("author", "article:author", "og:article:author") and content:
                result["has_author"] = True
            if prop == "article:published_time" and content:
                result["has_date"] = True

        return result

    # ------------------------------------------------------------------
    # JS rendering detection
    # ------------------------------------------------------------------

    def needs_js(self, html: str, threshold_words: int = 100) -> bool:
        """Return True if *html* appears to need JavaScript to render content.

        Checks:
        1. Explicit "enable JavaScript" messages.
        2. JS-framework root divs + sparse visible text.
        3. Many external script tags + near-empty body.
        4. Noscript blocks with meaningful content.
        """
        if not html or not html.strip():
            return False

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return False

        # Signal 1: explicit JS-required message (check BEFORE stripping)
        full_text_lower = soup.get_text(separator=" ").lower()
        if any(phrase in full_text_lower for phrase in _JS_REQUIRED_PHRASES):
            return True

        # Signal 4: noscript with meaningful text
        for noscript in soup.find_all("noscript"):
            ns_text = noscript.get_text(separator=" ")
            if len(ns_text.split()) > 15:
                return True

        # Strip noise before word count
        for tag in _NOISE_TAGS:
            for el in soup.find_all(tag):
                el.decompose()

        visible_text = soup.get_text(separator=" ")
        word_count = len(visible_text.split())

        # Signal 2: JS framework root + sparse text
        has_js_root = any(soup.select(sel) for sel in _JS_ROOT_SELECTORS)
        if has_js_root and word_count < threshold_words:
            return True

        # Signal 3: many external scripts + nearly empty
        # Re-parse original to count scripts (we decomposed above)
        try:
            soup2 = BeautifulSoup(html, "lxml")
            script_count = len(soup2.find_all("script", src=True))
            if script_count > 8 and word_count < 50:
                return True
        except Exception:
            pass

        return False

    # ------------------------------------------------------------------
    # Reading time
    # ------------------------------------------------------------------

    @staticmethod
    def reading_time(word_count: int, wpm: int = 200) -> int:
        """Return estimated reading time in minutes (minimum 1)."""
        return max(1, math.ceil(word_count / wpm))
