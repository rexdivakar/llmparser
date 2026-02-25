"""Deterministic metadata extraction from HTML.

Priority chain (highest → lowest):
    JSON-LD → Open Graph → Twitter Card → HTML <meta> tags → <title> / <html lang>
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import dateparser
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ISO_CLEANUP_RE = re.compile(r"\s+")


def _safe_str(val: Any, default: str = "") -> str:
    """Safely convert a BeautifulSoup attribute value (str | list | None) to str."""
    if val is None:
        return default
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val)


def _parse_date(raw: str | None) -> str | None:
    """Parse a date string to ISO 8601.

    Returns None on failure or when the year falls outside 1990-2099
    (catches epoch defaults like 1970-01-01 and far-future typos).
    """
    if not raw:
        return None
    raw = _ISO_CLEANUP_RE.sub(" ", raw.strip())
    try:
        parsed = dateparser.parse(
            raw,
            settings={
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DAY_OF_MONTH": "first",
                "PREFER_LOCALE_DATE_ORDER": False,
            },
        )
        if parsed:
            if not (1990 <= parsed.year <= 2099):
                return None
            return parsed.isoformat()
    except Exception as exc:
        logger.debug("Date parse failed for %r: %s", raw, exc)
    return None


def _first(*values: Any) -> Any:
    """Return the first non-empty, non-None value."""
    for v in values:
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

_ARTICLE_TYPES: frozenset[str] = frozenset(
    {
        "article",
        "blogging",
        "blogposting",
        "newsarticle",
        "techarticle",
        "scholarlyarticle",
        "liveblogposting",
        "reportage",
    },
)


def _extract_jsonld(soup: BeautifulSoup) -> dict:
    """Extract and merge relevant JSON-LD nodes from the page."""
    result: dict = {}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        nodes: list[dict] = []
        if isinstance(raw, list):
            nodes = raw
        elif isinstance(raw, dict):
            nodes = raw.get("@graph", [raw])

        for node in nodes:
            if not isinstance(node, dict):
                continue
            dtype = str(node.get("@type", "")).lower()
            if dtype not in _ARTICLE_TYPES and dtype not in {"webpage", "website"}:
                continue
            if dtype in _ARTICLE_TYPES or not result:
                result = node

    return result


def _author_from_jsonld(node: dict) -> str | None:
    author = node.get("author")
    if isinstance(author, dict):
        return author.get("name")
    if isinstance(author, list) and author:
        first = author[0]
        if isinstance(first, dict):
            return first.get("name")
        return str(first)
    if isinstance(author, str):
        return author
    return None


def _tags_from_jsonld(node: dict) -> list[str]:
    kw = node.get("keywords", [])
    if isinstance(kw, str):
        return [t.strip() for t in kw.split(",") if t.strip()]
    if isinstance(kw, list):
        return [str(k).strip() for k in kw if k]
    return []


# ---------------------------------------------------------------------------
# Open Graph / Twitter Card
# ---------------------------------------------------------------------------

def _extract_og_twitter(soup: BeautifulSoup) -> tuple[dict, dict]:
    og: dict = {}
    twitter: dict = {}

    for tag in soup.find_all("meta"):
        if not isinstance(tag, Tag):
            continue
        prop = _safe_str(tag.get("property") or tag.get("name"), "")
        content = _safe_str(tag.get("content"), "").strip()
        if not content:
            continue
        prop_lower = prop.lower()
        if prop_lower.startswith(("og:", "article:")):
            og[prop_lower] = content
        elif prop_lower.startswith("twitter:"):
            twitter[prop_lower] = content

    return og, twitter


# ---------------------------------------------------------------------------
# Article tag extraction
# ---------------------------------------------------------------------------

def _extract_tags(jsonld: dict, soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []

    tags.extend(_tags_from_jsonld(jsonld))

    for tag in soup.find_all("meta", property="article:tag"):
        if not isinstance(tag, Tag):
            continue
        val = _safe_str(tag.get("content"), "").strip()
        if val and val not in tags:
            tags.append(val)

    if not tags:
        kw_tag = soup.find("meta", attrs={"name": "keywords"})
        if kw_tag and isinstance(kw_tag, Tag):
            raw = _safe_str(kw_tag.get("content"), "")
            tags = [t.strip() for t in raw.split(",") if t.strip()]

    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        lower = t.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(t)
    return unique


# ---------------------------------------------------------------------------
# Canonical URL
# ---------------------------------------------------------------------------

def _extract_canonical(soup: BeautifulSoup, page_url: str) -> str | None:
    # Search for <link rel="canonical"> - rel is a multi-valued list in BS4
    for link in soup.find_all("link"):
        if not isinstance(link, Tag):
            continue
        rel_val = link.get("rel")
        if isinstance(rel_val, list) and "canonical" in rel_val:
            href = _safe_str(link.get("href"), "").strip()
            if href:
                return href if href.startswith("http") else urljoin(page_url, href)

    og_url = soup.find("meta", property="og:url")
    if og_url and isinstance(og_url, Tag):
        content = _safe_str(og_url.get("content"), "").strip()
        if content:
            return content
    return None


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

def _extract_language(soup: BeautifulSoup, og: dict, jsonld: dict) -> str | None:
    html_tag = soup.find("html")
    if html_tag and isinstance(html_tag, Tag):
        lang = _safe_str(html_tag.get("lang"), "").strip()
        if lang:
            return lang[:10]

    locale = og.get("og:locale", "")
    if locale:
        return locale.replace("_", "-").split("-")[0][:5]

    lang_jld = jsonld.get("inLanguage")
    if lang_jld:
        return str(lang_jld)[:10]

    meta_lang = soup.find("meta", attrs={"http-equiv": "content-language"})
    if not meta_lang:
        meta_lang = soup.find("meta", attrs={"name": "language"})
    if meta_lang and isinstance(meta_lang, Tag):
        content = _safe_str(meta_lang.get("content"), "").strip()
        if content:
            return content[:10]

    return None


# ---------------------------------------------------------------------------
# Images (og:image + JSON-LD image)
# ---------------------------------------------------------------------------

def _extract_images_meta(og: dict, jsonld: dict, page_url: str) -> list[dict]:
    images: list[dict] = []

    og_img = og.get("og:image", "")
    if og_img:
        url = og_img if og_img.startswith("http") else urljoin(page_url, og_img)
        images.append({"url": url, "alt": og.get("og:image:alt", ""), "caption": ""})

    jld_img = jsonld.get("image")
    existing_urls = {i["url"] for i in images}
    if isinstance(jld_img, str) and jld_img not in existing_urls:
        url = jld_img if jld_img.startswith("http") else urljoin(page_url, jld_img)
        images.append({"url": url, "alt": "", "caption": ""})
    elif isinstance(jld_img, dict):
        raw_url = jld_img.get("url", "")
        if raw_url and raw_url not in existing_urls:
            url = raw_url if raw_url.startswith("http") else urljoin(page_url, raw_url)
            images.append({"url": url, "alt": jld_img.get("description", ""), "caption": ""})

    return images


# ---------------------------------------------------------------------------
# Time tag helper
# ---------------------------------------------------------------------------

def _extract_time_datetime(soup: BeautifulSoup) -> str | None:
    """Return the datetime attribute of the first <time> element, if any."""
    time_tag = soup.find("time")
    if time_tag and isinstance(time_tag, Tag):
        return _safe_str(time_tag.get("datetime"), "").strip() or None
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_metadata(
    html: str,
    page_url: str = "",
    soup: BeautifulSoup | None = None,
) -> dict:
    """Extract all available metadata from *html*.

    Args:
        html:     Raw HTML string.
        page_url: Page URL used for canonical resolution and relative links.
        soup:     Pre-parsed BeautifulSoup object.  When provided the HTML is
                  not re-parsed, saving one parse per article.

    Returns a dict with keys:
        title, author, published_at, updated_at, site_name, language,
        summary, tags, canonical_url, images, raw_metadata
    """
    if soup is None:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return _empty_metadata()

    jsonld = _extract_jsonld(soup)
    og, twitter = _extract_og_twitter(soup)

    # ---- title ----
    title_tag = soup.find("title")
    h1_tag = soup.find("h1")
    title = _first(
        jsonld.get("headline"),
        jsonld.get("name"),
        og.get("og:title"),
        twitter.get("twitter:title"),
        title_tag.get_text().strip() if title_tag else None,
        h1_tag.get_text().strip() if h1_tag else None,
    )

    # ---- author ----
    author_meta = soup.find("meta", attrs={"name": "author"})
    author_from_meta = None
    if author_meta and isinstance(author_meta, Tag):
        author_from_meta = _safe_str(author_meta.get("content"), "").strip()
    author = _first(
        _author_from_jsonld(jsonld),
        og.get("article:author"),
        twitter.get("twitter:creator"),
        author_from_meta,
    )

    # ---- dates ----
    pubdate_meta = soup.find("meta", attrs={"name": "pubdate"})
    pubdate_raw = None
    if pubdate_meta and isinstance(pubdate_meta, Tag):
        pubdate_raw = _safe_str(pubdate_meta.get("content"), "")
    published_at = _parse_date(
        _first(
            jsonld.get("datePublished"),
            og.get("article:published_time"),
            pubdate_raw,
            _extract_time_datetime(soup),
        ),
    )
    updated_at = _parse_date(
        _first(
            jsonld.get("dateModified"),
            og.get("article:modified_time"),
            og.get("og:updated_time"),
        ),
    )

    # ---- site name ----
    publisher = jsonld.get("publisher")
    publisher_name = publisher.get("name") if isinstance(publisher, dict) else None
    site_name = _first(
        og.get("og:site_name"),
        publisher_name,
        urlparse(page_url).netloc.replace("www.", "") if page_url else None,
    )

    # ---- summary ----
    desc_meta = soup.find("meta", attrs={"name": "description"})
    desc_from_meta = None
    if desc_meta and isinstance(desc_meta, Tag):
        desc_from_meta = _safe_str(desc_meta.get("content"), "").strip()
    summary = _first(
        jsonld.get("description"),
        og.get("og:description"),
        twitter.get("twitter:description"),
        desc_from_meta,
    )

    # ---- language ----
    language = _extract_language(soup, og, jsonld)

    # ---- tags ----
    tags = _extract_tags(jsonld, soup)

    # ---- canonical URL ----
    canonical_url = _extract_canonical(soup, page_url)

    # ---- images ----
    images = _extract_images_meta(og, jsonld, page_url)

    return {
        "title": (title or "").strip(),
        "author": author,
        "published_at": published_at,
        "updated_at": updated_at,
        "site_name": site_name,
        "language": language,
        "summary": (summary or "").strip() or None,
        "tags": tags,
        "canonical_url": canonical_url,
        "images": images,
        "raw_metadata": {
            "jsonld": jsonld,
            "og": og,
            "twitter": twitter,
        },
    }


def _empty_metadata() -> dict:
    return {
        "title": "",
        "author": None,
        "published_at": None,
        "updated_at": None,
        "site_name": None,
        "language": None,
        "summary": None,
        "tags": [],
        "canonical_url": None,
        "images": [],
        "raw_metadata": {"jsonld": {}, "og": {}, "twitter": {}},
    }
