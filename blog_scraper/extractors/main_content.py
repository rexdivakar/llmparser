"""Main content extraction with three-tier cascade.

Tier 1: readability-lxml  (Mozilla Readability algorithm)
Tier 2: trafilatura       (second-opinion extractor)
Tier 3: DOM heuristic     (paragraph density + priority CSS selectors)
"""

from __future__ import annotations

import logging
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
        ]
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
        except Exception:
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
        return str(candidates[0][1])

    # Step 3: Full body fallback
    body = soup.find("body")
    return str(body) if body else html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_main_content(html: str, url: str = "") -> ExtractionResult:
    """Extract the main content from *html* using a three-tier cascade.

    Returns an ExtractionResult namedtuple:
        html     – extracted HTML fragment (may contain nested tags)
        method   – "readability" | "trafilatura" | "dom_heuristic"
        word_count – approximate word count of extracted text
    """
    # Tier 1: readability-lxml
    content = _try_readability(html, url)
    if content:
        wc = _count_words(content)
        logger.debug("readability extracted %d words from %s", wc, url)
        return ExtractionResult(html=content, method="readability", word_count=wc)

    # Tier 2: trafilatura
    content = _try_trafilatura(html, url)
    if content:
        wc = _count_words(content)
        logger.debug("trafilatura extracted %d words from %s", wc, url)
        return ExtractionResult(html=content, method="trafilatura", word_count=wc)

    # Tier 3: DOM heuristic
    content = dom_heuristic_extract(html)
    wc = _count_words(content)
    logger.debug("dom_heuristic extracted %d words from %s", wc, url)
    return ExtractionResult(html=content, method="dom_heuristic", word_count=wc)


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
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        if base_url:
            href = urljoin(base_url, href)

        if href in seen:
            continue
        seen.add(href)

        text = a.get_text().strip()
        rel_raw = a.get("rel")
        rel = " ".join(rel_raw) if isinstance(rel_raw, list) else str(rel_raw or "")

        try:
            is_internal = bool(
                base_domain and urlparse(href).netloc.lower() == base_domain.lower()
            )
        except Exception:
            is_internal = False

        links.append({"href": href, "text": text, "rel": rel, "is_internal": is_internal})

    return links
