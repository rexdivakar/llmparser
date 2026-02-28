"""llmparser.extractors.adaptive — Adaptive page classifier and fetch engine.

Identifies the type of web content, selects the optimal fetch strategy, and
falls back through a chain of strategies until content quality is acceptable.

Strategy chain (fastest/cheapest → slowest/heaviest):
  1. static       — urllib with full browser headers (no extra deps)
  2. amp           — fetch the AMP-equivalent URL (clean HTML, no JS needed)
  3. mobile_ua     — retry with an iPhone User-Agent (some sites serve simpler HTML)
  4. playwright    — headless Chromium for JS-rendered / cookie-walled pages
  5. best_effort   — return whatever static gave us (partial content logged)

Page types detected:
  STATIC_HTML   — plain HTML; content available without JavaScript
  JS_SPA        — JavaScript SPA (React, Next.js, Vue, Angular, Nuxt, Gatsby…)
  COOKIE_WALLED — GDPR / cookie-consent gate blocking the article body
  PAYWALLED     — subscription / login required
  UNKNOWN       — unclassified (strategy chosen based on content quality)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmparser.auth import AuthSession
    from llmparser.rate_limit import DomainRateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum body words for a static fetch to be considered "good enough"
_MIN_CONTENT_WORDS = 150

# Playwright requested but unavailable — warn once per session
_playwright_warned: dict[str, bool] = {"value": False}

# ---------------------------------------------------------------------------
# User-agents
# ---------------------------------------------------------------------------

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

# ---------------------------------------------------------------------------
# JS framework fingerprints (matched against all <script> src + inline text)
# ---------------------------------------------------------------------------

_JS_FRAMEWORK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Next.js",   re.compile(r"/_next/static/|window\.__NEXT_DATA__", re.IGNORECASE)),
    ("Nuxt.js",   re.compile(r"/__nuxt/|window\.__NUXT__", re.IGNORECASE)),
    ("React/CRA", re.compile(r"/static/js/main\.[a-f0-9]+\.js", re.IGNORECASE)),
    ("Webpack",   re.compile(r"chunk\.[a-f0-9]+\.js", re.IGNORECASE)),
    ("Angular",   re.compile(r"angular(?:\.min)?\.js|ng-app", re.IGNORECASE)),
    ("Vue",       re.compile(r"vue(?:\.min)?\.js|data-v-app", re.IGNORECASE)),
    ("Ember",     re.compile(r"ember(?:\.min)?\.js", re.IGNORECASE)),
    ("Gatsby",    re.compile(r"gatsby-focus-wrapper|window\.__gatsby", re.IGNORECASE)),
    ("Svelte",    re.compile(r"svelte(?:kit)?|__svelte", re.IGNORECASE)),
    ("Remix",     re.compile(r"__remixContext", re.IGNORECASE)),
    ("Astro",     re.compile(r"astro-island|astro:page-load", re.IGNORECASE)),
]

# IDs of root elements used by JS frameworks
_JS_ROOT_ID_RE = re.compile(
    r"^(root|app|__next|__nuxt|app-root|gatsby-focus-wrapper|ember-application)$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Paywall selectors / phrases
# ---------------------------------------------------------------------------

_PAYWALL_CSS = (
    ".paywall", ".paid-content", ".premium-content",
    "#piano-paywall", ".tp-modal", ".tp-iframe-wrapper",
    ".subscriber-only", ".metered-paywall",
    "[class*='paywall']", "[id*='paywall']",
    ".subscription-required", ".access-denied",
    ".piano-container", ".reg-wall",
)

_PAYWALL_PHRASES = frozenset([
    "subscribe to continue",
    "subscribe to read",
    "sign in to read",
    "this article is for subscribers",
    "become a member to",
    "unlock this article",
    "member-only content",
    "you've reached your free article limit",
    "you have read your free articles",
    "subscribe for unlimited",
    "create a free account to continue",
])

# ---------------------------------------------------------------------------
# Cookie-wall phrases
# ---------------------------------------------------------------------------

_COOKIE_WALL_PHRASES = frozenset([
    "cookie preferences",
    "essential cookies enable",
    "cookie consent",
    "manage your cookie",
    "accept all cookies",
    "reject all cookies",
    "cookieyes",
    "cookiebot",
])

# Tags stripped before body-word-count for accurate signal detection
_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "aside")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class PageType(StrEnum):
    STATIC_HTML   = "static_html"
    JS_SPA        = "js_spa"
    COOKIE_WALLED = "cookie_walled"
    PAYWALLED     = "paywalled"
    UNKNOWN       = "unknown"


@dataclass
class PageSignals:
    """Raw signals extracted from the page HTML."""
    body_word_count: int = 0
    has_meta_title: bool = False
    has_article_schema: bool = False
    is_js_spa: bool = False
    is_cookie_walled: bool = False
    is_paywalled: bool = False
    amp_url: str | None = None
    feed_url: str | None = None
    frameworks_detected: list[str] = field(default_factory=list)
    js_root_found: bool = False


@dataclass
class ClassificationResult:
    """Full classification of a fetched page."""
    page_type: PageType
    signals: PageSignals
    recommended_strategy: str   # "static"|"amp"|"mobile_ua"|"playwright"
    confidence: float           # 0.0-1.0
    reason: str                 # Human-readable explanation


@dataclass
class FetchResult:
    """Result of an adaptive fetch."""
    html: str
    classification: ClassificationResult
    strategy_used: str


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def _raw_word_count(html: str) -> int:
    """Word count from raw HTML via regex (no BS4, used as fallback)."""
    return len(re.sub(r"<[^>]+>", " ", html).split())


def _detect_signals(html: str, url: str = "") -> PageSignals:
    """Extract all classification signals from *html*.

    Performs two lightweight BS4 parses:
      - *soup_full*: original HTML for structural signals (needs <script> tags)
      - *soup_text*: noise-stripped for accurate visible word count
    """
    signals = PageSignals()

    try:
        from bs4 import BeautifulSoup
        from bs4 import Tag as BSTag
    except ImportError:
        signals.body_word_count = _raw_word_count(html)
        return signals

    # ── Parse 1: full HTML for structural signals ────────────────────────────
    try:
        soup_full = BeautifulSoup(html, "lxml")
    except Exception as exc:
        logger.debug("BS4 parse failed in adaptive._detect_signals: %s", exc)
        signals.body_word_count = _raw_word_count(html)
        return signals

    # ── Parse 2: stripped HTML for accurate content word count ──────────────
    # Strip noise tags, <template> placeholders, and cookie-consent widgets
    # so the count reflects real article content only.
    #
    # IMPORTANT: lxml moves <template> children into the document body, so
    # decompose() on the tag leaf does not remove those children.  We must
    # strip <template> blocks via regex BEFORE parsing.
    try:
        from llmparser.extractors.main_content import _strip_cookie_consent
        clean_html = re.sub(
            r"<template\b[^>]*>.*?</template>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        soup_text = BeautifulSoup(clean_html, "lxml")
        for tag_name in _NOISE_TAGS:
            for el in soup_text.find_all(tag_name):
                el.decompose()
        _strip_cookie_consent(soup_text)
        body = soup_text.find("body")
        signals.body_word_count = len(
            ((body or soup_text).get_text(separator=" ")).split(),
        )
    except Exception:
        signals.body_word_count = _raw_word_count(html)

    # ── Meta title ───────────────────────────────────────────────────────────
    signals.has_meta_title = bool(
        soup_full.find("meta", attrs={"property": "og:title"})
        or soup_full.find("title"),
    )

    # ── JSON-LD article schema ───────────────────────────────────────────────
    for script in soup_full.find_all("script", type="application/ld+json"):
        txt = script.get_text() or ""
        if any(t in txt for t in ("Article", "BlogPosting", "NewsArticle")):
            signals.has_article_schema = True
            break

    # ── AMP URL ──────────────────────────────────────────────────────────────
    for link in soup_full.find_all("link"):
        if not isinstance(link, BSTag):
            continue
        rel = link.get("rel")
        if isinstance(rel, list) and "amphtml" in rel:
            href = str(link.get("href") or "").strip()
            if href:
                signals.amp_url = href
            break

    # ── RSS / Atom feed ──────────────────────────────────────────────────────
    for link in soup_full.find_all("link"):
        if not isinstance(link, BSTag):
            continue
        rel = link.get("rel")
        if not (isinstance(rel, list) and "alternate" in rel):
            continue
        ltype = str(link.get("type") or "").lower()
        if "rss" in ltype or "atom" in ltype:
            href = str(link.get("href") or "").strip()
            if href:
                signals.feed_url = href
            break

    # ── JS framework fingerprints ────────────────────────────────────────────
    all_script_text = " ".join(
        str(s.get("src") or "") + " " + (s.string or "")
        for s in soup_full.find_all("script")
    )
    for name, pat in _JS_FRAMEWORK_PATTERNS:
        if pat.search(all_script_text):
            signals.frameworks_detected.append(name)

    # ── JS SPA root divs (nearly empty) ─────────────────────────────────────
    for el in soup_full.find_all(id=_JS_ROOT_ID_RE):
        if not isinstance(el, BSTag):
            continue
        if len(el.get_text(separator=" ").split()) < 20:
            signals.js_root_found = True
            break

    if (signals.frameworks_detected and (
        signals.js_root_found or signals.body_word_count < 100
    )) or (signals.body_word_count < 10 and bool(soup_full.find("script", src=True))):
        signals.is_js_spa = True

    # ── Cookie wall + paywall (share one body-text extraction) ───────────────
    try:
        body_lower = (
            (soup_full.find("body") or soup_full).get_text(separator=" ").lower()
        )
        cookie_hits = sum(1 for p in _COOKIE_WALL_PHRASES if p in body_lower)
        # Trigger on phrase hits alone — cookie phrases are specific enough.
        # Also trigger when content is thin after stripping consent elements.
        if cookie_hits >= 2 or (cookie_hits >= 1 and signals.body_word_count < 150):
            signals.is_cookie_walled = True

        if not signals.is_cookie_walled:
            paywall_hits = sum(1 for p in _PAYWALL_PHRASES if p in body_lower)
            if paywall_hits >= 1:
                signals.is_paywalled = True
            else:
                for sel in _PAYWALL_CSS:
                    try:
                        if soup_full.select(sel):
                            signals.is_paywalled = True
                            break
                    except Exception as exc:
                        logger.debug("CSS selector parse error for %r: %s", sel, exc)
    except Exception as exc:
        logger.debug("Cookie/paywall signal extraction failed: %s", exc)

    return signals


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_page(html: str, url: str = "") -> ClassificationResult:
    """Classify *html* and recommend the best fetch strategy.

    Decision tree (priority order):
      JS SPA > Cookie wall > Paywall > AMP available > Good static > Thin static
    """
    sig = _detect_signals(html, url)

    # ── 1: JS SPA ─────────────────────────────────────────────────────────────
    if sig.is_js_spa:
        strategy = "amp" if sig.amp_url else "playwright"
        fw = ", ".join(sig.frameworks_detected) or "ultra-thin body + scripts"
        confidence = 0.90 if sig.frameworks_detected else 0.80
        return ClassificationResult(
            page_type=PageType.JS_SPA,
            signals=sig,
            recommended_strategy=strategy,
            confidence=confidence,
            reason=f"JS SPA ({fw}); visible body={sig.body_word_count} words → {strategy}",
        )

    # ── 2: Cookie wall ───────────────────────────────────────────────────────
    if sig.is_cookie_walled:
        return ClassificationResult(
            page_type=PageType.COOKIE_WALLED,
            signals=sig,
            recommended_strategy="playwright",
            confidence=0.85,
            reason=(
                f"Cookie-consent wall detected; "
                f"visible body={sig.body_word_count} words"
            ),
        )

    # ── 3: Paywall ───────────────────────────────────────────────────────────
    if sig.is_paywalled and sig.body_word_count < 500:
        return ClassificationResult(
            page_type=PageType.PAYWALLED,
            signals=sig,
            recommended_strategy="playwright",
            confidence=0.75,
            reason="Paywall detected — Playwright may bypass soft paywalls",
        )

    # ── 4: AMP available, thin static ────────────────────────────────────────
    if sig.amp_url and sig.body_word_count < _MIN_CONTENT_WORDS:
        return ClassificationResult(
            page_type=PageType.STATIC_HTML,
            signals=sig,
            recommended_strategy="amp",
            confidence=0.70,
            reason=(
                f"AMP URL found; thin static body ({sig.body_word_count} words) "
                "→ amp"
            ),
        )

    # ── 5: Good static page ──────────────────────────────────────────────────
    if sig.body_word_count >= _MIN_CONTENT_WORDS:
        return ClassificationResult(
            page_type=PageType.STATIC_HTML,
            signals=sig,
            recommended_strategy="static",
            confidence=0.90,
            reason=f"Static HTML; {sig.body_word_count} body words — no JS needed",
        )

    # ── 6: Thin, metadata present — try mobile UA before Playwright ──────────
    if sig.has_meta_title and sig.body_word_count < _MIN_CONTENT_WORDS:
        strategy = "amp" if sig.amp_url else "mobile_ua"
        return ClassificationResult(
            page_type=PageType.UNKNOWN,
            signals=sig,
            recommended_strategy=strategy,
            confidence=0.50,
            reason=(
                f"Thin content ({sig.body_word_count} words), "
                f"metadata present → {strategy}"
            ),
        )

    return ClassificationResult(
        page_type=PageType.STATIC_HTML,
        signals=sig,
        recommended_strategy="static",
        confidence=0.55,
        reason=f"Default static ({sig.body_word_count} body words)",
    )


# ---------------------------------------------------------------------------
# Adaptive fetch engine
# ---------------------------------------------------------------------------

def adaptive_fetch_html(
    url: str,
    *,
    timeout: int = 30,
    user_agent: str | None = None,
    proxy: str | None = None,
    auth: AuthSession | None = None,
    rate_limiter: DomainRateLimiter | None = None,
    playwright_page_methods: list[dict] | None = None,
) -> FetchResult:
    """Fetch *url* using the best available strategy.

    Strategy chain (fastest → slowest):
        static → amp → mobile_ua → playwright → playwright_fallback → best_effort

    Args:
        url:        Fully-qualified HTTP/HTTPS URL.
        timeout:    Per-request timeout in seconds (Playwright gets max(timeout,60)).
        user_agent: Override the default browser User-Agent string.
        proxy:      Optional proxy URL forwarded to every fetch attempt in the
                    strategy chain (e.g. ``"http://host:port"``).

    Returns:
        :class:`FetchResult` with html, classification, and strategy_used.

    Raises:
        FetchError: Only when even the static fetch fails completely.
    """
    # Import lazily to avoid circular imports (query imports adaptive lazily too)
    from llmparser.query import fetch_html as _static

    # ── Step 1: Static fetch (always first) ──────────────────────────────────
    html = _static(
        url,
        timeout=timeout,
        user_agent=user_agent,
        proxy=proxy,
        auth=auth,
        rate_limiter=rate_limiter,
    )
    classification = classify_page(html, url)
    strategy = classification.recommended_strategy

    logger.info(
        "Classified %s → type=%s strategy=%s confidence=%.2f | %s",
        url,
        classification.page_type.value,
        strategy,
        classification.confidence,
        classification.reason,
    )

    # Already good quality — return immediately
    if (
        strategy == "static"
        and classification.signals.body_word_count >= _MIN_CONTENT_WORDS
    ):
        return FetchResult(html=html, classification=classification, strategy_used="static")

    # ── Step 2: AMP (clean HTML, no JS required) ─────────────────────────────
    if strategy == "amp" and classification.signals.amp_url:
        try:
            amp_html = _static(
                classification.signals.amp_url,
                timeout=timeout,
                user_agent=user_agent,
                proxy=proxy,
                auth=auth,
                rate_limiter=rate_limiter,
            )
            if _raw_word_count(amp_html) > classification.signals.body_word_count:
                logger.info("AMP strategy succeeded for %s", url)
                return FetchResult(
                    html=amp_html,
                    classification=classification,
                    strategy_used="amp",
                )
        except Exception as exc:
            logger.debug("AMP fetch failed for %s: %s", url, exc)

    # ── Step 3: Mobile User-Agent ─────────────────────────────────────────────
    if strategy == "mobile_ua":
        try:
            mob_html = _static(
                url,
                timeout=timeout,
                user_agent=_MOBILE_UA,
                proxy=proxy,
                auth=auth,
                rate_limiter=rate_limiter,
            )
            if _raw_word_count(mob_html) > classification.signals.body_word_count * 1.3:
                logger.info("Mobile-UA strategy succeeded for %s", url)
                return FetchResult(
                    html=mob_html,
                    classification=classification,
                    strategy_used="mobile_ua",
                )
        except Exception as exc:
            logger.debug("Mobile-UA fetch failed for %s: %s", url, exc)

    # ── Step 4: Playwright (JS render) ───────────────────────────────────────
    if strategy == "playwright":
        pw_html = _try_playwright(
            url,
            timeout=timeout,
            proxy=proxy,
            user_agent=user_agent,
            auth=auth,
            page_methods=playwright_page_methods,
            rate_limiter=rate_limiter,
        )
        if pw_html and _raw_word_count(pw_html) > classification.signals.body_word_count:
            logger.info("Playwright strategy succeeded for %s", url)
            return FetchResult(
                html=pw_html,
                classification=classification,
                strategy_used="playwright",
            )

    # ── Step 5: Playwright fallback (thin static that slipped through) ────────
    if (
        strategy != "playwright"
        and classification.signals.body_word_count < _MIN_CONTENT_WORDS
    ):
        pw_html = _try_playwright(
            url,
            timeout=timeout,
            proxy=proxy,
            user_agent=user_agent,
            auth=auth,
            page_methods=playwright_page_methods,
            rate_limiter=rate_limiter,
        )
        if pw_html and _raw_word_count(pw_html) > classification.signals.body_word_count:
            logger.info("Playwright fallback succeeded for %s", url)
            return FetchResult(
                html=pw_html,
                classification=classification,
                strategy_used="playwright_fallback",
            )

    # ── Step 6: Registered strategy plugins ──────────────────────────────────
    try:
        from llmparser.plugins import get_strategies
        for plugin in get_strategies():
            if plugin.can_handle(url, classification.signals):
                try:
                    plugin_html = plugin.fetch(url, timeout=timeout)
                    if (
                        plugin_html
                        and _raw_word_count(plugin_html)
                        > classification.signals.body_word_count
                    ):
                        logger.info("Plugin strategy %s succeeded for %s", plugin.name, url)
                        return FetchResult(
                            html=plugin_html,
                            classification=classification,
                            strategy_used=plugin.name,
                        )
                except Exception as exc:
                    logger.warning("Plugin strategy %s failed for %s: %s", plugin.name, url, exc)
    except Exception as exc:
        logger.debug("Error iterating strategy plugins: %s", exc)

    # ── Best effort ───────────────────────────────────────────────────────────
    logger.warning(
        "All strategies exhausted for %s — returning best-effort result (%d words)",
        url,
        classification.signals.body_word_count,
    )
    return FetchResult(
        html=html,
        classification=classification,
        strategy_used="static_best_effort",
    )


def _try_playwright(
    url: str,
    timeout: int = 30,
    proxy: str | None = None,
    user_agent: str | None = None,
    auth: AuthSession | None = None,
    page_methods: list[dict] | None = None,
    rate_limiter: DomainRateLimiter | None = None,
) -> str | None:
    """Attempt a Playwright fetch; return HTML string or None on any failure."""
    try:
        from llmparser.query import _fetch_html_playwright
        return _fetch_html_playwright(
            url,
            timeout=max(timeout, 60),
            proxy=proxy,
            user_agent=user_agent,
            auth=auth,
            page_methods=page_methods,
            rate_limiter=rate_limiter,
        )
    except ImportError:
        if not _playwright_warned["value"]:
            logger.warning(
                "Playwright not installed - install with: "
                "pip install playwright && playwright install chromium",
            )
            _playwright_warned["value"] = True
        return None
    except Exception as exc:
        logger.warning("Playwright fetch failed for %s: %s", url, exc)
        return None
