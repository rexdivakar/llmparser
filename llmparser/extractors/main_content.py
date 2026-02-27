"""Main content extraction with three-tier cascade.

Tier 1: readability-lxml  (Mozilla Readability algorithm)
Tier 2: trafilatura       (second-opinion extractor)
Tier 3: DOM heuristic     (paragraph density + priority CSS selectors)
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import NamedTuple

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Minimum words for a tier's output to be considered successful
_READABILITY_MIN_WORDS = 50
_TRAFILATURA_MIN_WORDS = 30
_DOM_MIN_WORDS = 10

# Priority CSS selectors for DOM heuristic (tried in order)
_CONTENT_SELECTORS: tuple[str, ...] = (
    "article",
    "main",
    '[role="main"]',
    '[itemprop="articleBody"]',
    ".post-content",
    ".article-content",
    ".entry-content",
    ".post-body",
    ".article-body",
    "#article-content",
    "#post-content",
    "#entry-content",
    "#content",
    "#main-content",
    ".content-body",
    ".story-body",
    ".blog-post",
    ".post",
    ".single-content",
)

# Tags to strip as boilerplate before DOM heuristic scoring
_BOILERPLATE_TAGS: tuple[str, ...] = (
    "nav",
    "header",
    "footer",
    "aside",
    "script",
    "style",
    "noscript",
    "form",
    "button",
    "input",
    "select",
    "textarea",
)

# Class/id substrings that indicate non-content elements
_NOISE_SUBSTRINGS: tuple[str, ...] = (
    "sidebar",
    "comment",
    "advertisement",
    "banner",
    "promo",
    "related",
    "share",
    "social",
    "newsletter",
    "cookie",
    "popup",
    "modal",
    "widget",
)

# ---------------------------------------------------------------------------
# Cookie-consent / GDPR overlay removal
# ---------------------------------------------------------------------------

# CSS selectors for known cookie-consent widgets — removed before any extractor runs
_COOKIE_CONSENT_SELECTORS: tuple[str, ...] = (
    # CookieYes / CookieLawInfo
    ".cky-consent-container", ".cookieyes-modal",
    "#cookie-law-info-bar", ".cli-modal", ".cli-settings-overlay",
    # Cookiebot
    "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyContent",
    # OneTrust
    "#onetrust-consent-sdk", "#onetrust-banner-sdk", "#onetrust-pc-sdk",
    # Complianz
    "#cmplz-cookiebanner-container", ".cmplz-cookiebanner",
    # Borlabs
    "#BorlabsCookieBox",
    # WP GDPR Cookie Notice
    "#cookie_notice", "#gdpr-cookie-notice",
    # Generic
    ".cookie-banner", ".cookie-notice", ".cookie-popup",
    ".cookie-modal", ".cookie-overlay", ".cookie-consent",
    "#cookie-notice", "#cookie-banner", "#cookie-popup",
    ".gdpr-overlay", "#gdpr_overlay", ".gdpr-banner",
    "[aria-label='cookieconsent']",
)

# Class/id keywords that reliably identify consent widgets (substring match)
_CONSENT_WIDGET_KEYWORDS: tuple[str, ...] = (
    "cookieyes", "cookiebot", "cookiehub", "onetrust",
    "borlabs", "complianz", "cookielawinfo", "cky-",
    "wpconsent",          # WPConsent plugin (renders inside <template>)
    "cookie-consent",
    "gdpr-consent",
)


def _strip_cookie_consent(soup: BeautifulSoup) -> None:
    """Remove cookie-consent / GDPR overlay elements from *soup* in-place."""
    # Named selectors first (fast, specific)
    for selector in _COOKIE_CONSENT_SELECTORS:
        with contextlib.suppress(Exception):
            for el in soup.select(selector):
                if isinstance(el, Tag):
                    el.decompose()

    # Keyword sweep — catches dynamically-named widgets
    for el in list(soup.find_all(True)):
        if not isinstance(el, Tag):
            continue
        combined = (
            " ".join(el.get("class") or []) + " " + str(el.get("id") or "")
        ).lower()
        if any(kw in combined for kw in _CONSENT_WIDGET_KEYWORDS):
            el.decompose()


def _preprocess_html(html: str) -> str:
    """Strip cookie-consent overlays and template placeholders from *html*.

    Also removes ``<template>`` elements: HTML5 templates hold pre-render
    placeholder content that is never visible to users, but readability-lxml
    can mistakenly extract it as the "main content" (e.g. WPConsent modals).

    Note: lxml re-parents ``<template>`` children into the document body, so
    BS4 ``decompose()`` on the tag leaf does not remove the children.  We
    therefore strip ``<template>`` blocks with a regex pass *before* parsing.
    """
    # Regex pass first — removes <template>…</template> including all content.
    # Must happen before BS4/lxml parsing because lxml moves template children
    # into the document body, making decompose() ineffective on the container.
    with contextlib.suppress(Exception):
        html = re.sub(
            r"<template\b[^>]*>.*?</template>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

    try:
        soup = BeautifulSoup(html, "lxml")
        _strip_cookie_consent(soup)
        return str(soup)
    except Exception as exc:
        logger.debug("HTML pre-processing failed: %s", exc)
        return html


class ExtractionResult(NamedTuple):
    html: str
    method: str
    word_count: int


def _count_words(html: str) -> int:
    try:
        soup = BeautifulSoup(html, "lxml")
        return len(soup.get_text(separator=" ").split())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Tier 1: readability-lxml
# ---------------------------------------------------------------------------

def _try_readability(html: str, url: str = "") -> str | None:
    try:
        from readability import Document  # type: ignore[import-untyped]

        doc = Document(html, url=url)
        content = doc.summary(html_partial=False)
        if _count_words(content) >= _READABILITY_MIN_WORDS:
            return content
    except Exception as exc:
        logger.debug("readability failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Tier 2: trafilatura
# ---------------------------------------------------------------------------

def _try_trafilatura(html: str, url: str = "") -> str | None:
    try:
        import trafilatura  # type: ignore[import-untyped]

        content = trafilatura.extract(
            html,
            include_links=True,
            include_images=True,
            include_tables=True,
            output_format="html",
            url=url or None,
            favor_recall=True,
            no_fallback=False,
        )
        if content and _count_words(content) >= _TRAFILATURA_MIN_WORDS:
            return content
    except Exception as exc:
        logger.debug("trafilatura failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Tier 3: DOM heuristic
# ---------------------------------------------------------------------------

def _is_noisy_element(tag: Tag) -> bool:
    """Return True if *tag* appears to be boilerplate/noise."""
    combined = " ".join(
        [
            " ".join(tag.get("class") or []),
            str(tag.get("id") or ""),
            str(tag.get("role") or ""),
        ],
    ).lower()
    return any(noise in combined for noise in _NOISE_SUBSTRINGS)


def _paragraph_density_score(tag: Tag) -> tuple[int, int]:
    """Return (paragraph_words, total_words) for density scoring."""
    paragraphs = tag.find_all("p")
    para_text = " ".join(p.get_text(separator=" ") for p in paragraphs)
    para_words = len(para_text.split())
    total_words = len(tag.get_text(separator=" ").split())
    return para_words, total_words


def dom_heuristic_extract(html: str) -> str:
    """Extract main content using DOM heuristics when readability/trafilatura fail.

    Algorithm:
    1. Strip boilerplate elements.
    2. Try priority CSS selectors; pick first match with ≥ DOM_MIN_WORDS.
    3. Score all <div>/<section> by paragraph density; pick highest.
    4. Fall back to <body>.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return html

    # Step 0: Strip boilerplate tags
    for tag_name in _BOILERPLATE_TAGS:
        for el in soup.find_all(tag_name):
            el.decompose()

    # Strip noise elements by class/id
    for el in soup.find_all(["div", "section", "aside"]):
        if isinstance(el, Tag) and _is_noisy_element(el):
            el.decompose()

    # Step 1: Priority selectors
    for selector in _CONTENT_SELECTORS:
        try:
            elements = soup.select(selector)
        except Exception as exc:
            logger.debug("CSS selector %r failed: %s", selector, exc)
            continue
        if not elements:
            continue
        best = max(elements, key=lambda e: len(e.get_text(separator=" ").split()))
        if isinstance(best, Tag):
            word_count = len(best.get_text(separator=" ").split())
            if word_count >= _DOM_MIN_WORDS:
                return str(best)

    # Step 2: Paragraph density scoring across <div> and <section>
    candidates: list[tuple[float, Tag]] = []
    for el in soup.find_all(["div", "section"]):
        if not isinstance(el, Tag):
            continue
        para_words, total_words = _paragraph_density_score(el)
        if para_words < _DOM_MIN_WORDS:
            continue
        density = para_words / max(total_words, 1)
        score = para_words * density
        candidates.append((score, el))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_el = candidates[0][1]

        # If one element is clearly dominant (holds ≥ 55 % of remaining body
        # words), return just that element.  Otherwise the content is spread
        # across many equal-weight sections (service pages, wikis, portals) —
        # return the full stripped body so nothing is left behind.
        body = soup.find("body")
        body_wc = len((body or soup).get_text(separator=" ").split()) if body else 0
        top_wc = len(top_el.get_text(separator=" ").split())
        if body_wc == 0 or top_wc / body_wc >= 0.55:
            return str(top_el)
        return str(body) if body else str(top_el)

    # Step 3: Full body fallback
    body = soup.find("body")
    return str(body) if body else html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_main_content(html: str, url: str = "") -> ExtractionResult:
    """Extract the main content from *html* using a best-of-two + heuristic cascade.

    Strategy:
      1. Run readability-lxml AND trafilatura independently.
      2. If trafilatura returns >= 1.4x more words than readability, prefer it -
         this handles multi-section service/portal pages where readability
         fixates on one content block and discards the rest.
      3. Use whichever of readability/trafilatura met its minimum threshold.
      4. Fall back to DOM heuristic (returns full body when no element
         dominates, preserving equal-weight sections).

    Returns an ExtractionResult namedtuple:
        html       - extracted HTML fragment
        method     - "readability" | "trafilatura" | "dom_heuristic"
        word_count - approximate word count of extracted text
    """
    # Pre-process: strip cookie-consent / GDPR overlays before any extractor
    html = _preprocess_html(html)

    # Run both Tier-1 and Tier-2 extractors so we can compare their output.
    r_content = _try_readability(html, url)
    r_wc = _count_words(r_content) if r_content else 0

    t_content = _try_trafilatura(html, url)
    t_wc = _count_words(t_content) if t_content else 0

    logger.debug(
        "readability=%d words  trafilatura=%d words  url=%s", r_wc, t_wc, url,
    )

    # Both extractors produced usable content — pick the richer one.
    if r_wc >= _READABILITY_MIN_WORDS and t_wc >= _TRAFILATURA_MIN_WORDS:
        # Trafilatura gets the nod when it yields >= 40 % more words.
        # Threshold of 1.4x avoids switching on minor noise differences.
        if t_wc >= r_wc * 1.4:
            logger.debug("trafilatura wins (%d vs %d words) for %s", t_wc, r_wc, url)
            assert t_content is not None  # r_wc>0 implies t_content was truthy
            return ExtractionResult(html=t_content, method="trafilatura", word_count=t_wc)
        logger.debug("readability wins (%d vs %d words) for %s", r_wc, t_wc, url)
        assert r_content is not None  # r_wc>0 implies r_content was truthy
        return ExtractionResult(html=r_content, method="readability", word_count=r_wc)

    # Only one extractor succeeded.
    if r_wc >= _READABILITY_MIN_WORDS:
        assert r_content is not None  # r_wc>0 implies r_content was truthy
        return ExtractionResult(html=r_content, method="readability", word_count=r_wc)
    if t_wc >= _TRAFILATURA_MIN_WORDS:
        assert t_content is not None  # t_wc>0 implies t_content was truthy
        return ExtractionResult(html=t_content, method="trafilatura", word_count=t_wc)

    # Tier 3: DOM heuristic (handles pages both extractors can't parse)
    content = dom_heuristic_extract(html)
    wc = _count_words(content)
    logger.debug("dom_heuristic extracted %d words from %s", wc, url)
    result = ExtractionResult(html=content, method="dom_heuristic", word_count=wc)

    # Tier 4: Extractor plugins (tried after built-in cascade)
    try:
        from llmparser.plugins import get_extractors
        for plugin in sorted(get_extractors(), key=lambda p: p.priority, reverse=True):
            if plugin.can_extract(html, url):
                try:
                    plugin_html = plugin.extract(html, url)
                    if plugin_html:
                        plugin_wc = _count_words(plugin_html)
                        if plugin_wc > result.word_count:
                            result = ExtractionResult(
                                html=plugin_html,
                                method=plugin.name,
                                word_count=plugin_wc,
                            )
                            break
                except Exception as exc:
                    logger.warning("Extractor plugin %s failed: %s", plugin.name, exc)
    except Exception as exc:
        logger.debug("Error iterating extractor plugins: %s", exc)

    return result


def extract_images(html: str, base_url: str = "") -> list[dict]:
    """Extract all images from *html* with URL, alt, and caption."""
    from urllib.parse import urljoin

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    images: list[dict] = []
    seen_urls: set[str] = set()

    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue

        src = str(img.get("src") or "").strip()
        if not src:
            # Try srcset
            srcset = str(img.get("srcset") or "").strip()
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        if not src:
            continue

        if base_url:
            src = urljoin(base_url, src)
        if src in seen_urls:
            continue
        seen_urls.add(src)

        alt = str(img.get("alt") or "").strip()

        # Caption: look for <figcaption> in parent <figure>
        caption = ""
        parent = img.parent
        if isinstance(parent, Tag) and parent.name == "figure":
            figcaption = parent.find("figcaption")
            if figcaption:
                caption = figcaption.get_text().strip()

        images.append({"url": src, "alt": alt, "caption": caption})

    return images


def extract_links(html: str, base_url: str = "", base_domain: str = "") -> list[dict]:
    """Extract all hyperlinks from *html*."""
    from urllib.parse import urljoin, urlparse

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    links: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = str(a.get("href") or "").strip()
        if not href:
            continue
        href_lower = href.lower()
        if href_lower.startswith(("#", "mailto:", "javascript:", "tel:", "data:", "sms:")):
            continue

        if base_url:
            href = urljoin(base_url, href)
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme not in ("http", "https"):
            continue

        if href in seen:
            continue
        seen.add(href)

        text = a.get_text().strip()
        rel_raw = a.get("rel")
        rel = " ".join(rel_raw) if isinstance(rel_raw, list) else str(rel_raw or "")

        try:
            is_internal = bool(
                base_domain and urlparse(href).netloc.lower() == base_domain.lower(),
            )
        except Exception:
            is_internal = False

        links.append({"href": href, "text": text, "rel": rel, "is_internal": is_internal})

    return links
