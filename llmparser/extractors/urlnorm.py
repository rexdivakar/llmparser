"""URL normalization and slug generation utilities."""

from __future__ import annotations

import re
from urllib.parse import (
    ParseResult,
    parse_qs,
    urlencode,
    urlparse,
    urlunparse,
)

# Query parameters that carry no semantic meaning for content identity
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_reader",
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "ref",
        "source",
        "via",
        "_ga",
        "_gac",
        "mc_cid",
        "mc_eid",
        "igshid",
        "s_kwcid",
        "ef_id",
        "affiliate_id",
        "clickid",
    },
)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ftp": 21}

# Characters allowed in slugs
_SLUG_SAFE_RE = re.compile(r"[^\w\-]")
_MULTI_DASH_RE = re.compile(r"-{2,}")
_LEADING_TRAILING_DASH_RE = re.compile(r"^-+|-+$")

# Non-content URL extensions
NON_CONTENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".bmp",
        ".tiff",
        ".ico",
        ".css",
        ".js",
        ".json",
        ".xml",
        ".txt",
        ".csv",
        ".zip",
        ".tar",
        ".gz",
        ".rar",
        ".7z",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
    },
)


def normalize_url(url: str) -> str:
    """Return a canonical form of *url* suitable for deduplication.

    Transformations applied:
    - Lowercase scheme and host
    - Remove default ports
    - Strip URL fragment
    - Remove known tracking query parameters
    - Sort remaining query parameters
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()

    # Strip default port from netloc
    if ":" in netloc:
        host, _, port_str = netloc.rpartition(":")
        try:
            port = int(port_str)
            if _DEFAULT_PORTS.get(scheme) == port:
                netloc = host
        except ValueError:
            pass

    # Clean query parameters
    raw_qs = parsed.query
    if raw_qs:
        qs = parse_qs(raw_qs, keep_blank_values=True)
        cleaned = {
            k: v
            for k, v in qs.items()
            if k.lower() not in _TRACKING_PARAMS
        }
        new_query = urlencode(sorted(cleaned.items()), doseq=True)
    else:
        new_query = ""

    normalized = ParseResult(
        scheme=scheme,
        netloc=netloc,
        path=parsed.path,
        params=parsed.params,
        query=new_query,
        fragment="",  # always strip fragment
    )
    return urlunparse(normalized)


def url_to_slug(url: str, max_length: int = 100) -> str:
    """Convert a URL into a filesystem-safe slug.

    Example:
        https://example.com/blog/how-to-scrape-data → blog-how-to-scrape-data
    """
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            path = parsed.netloc.replace(".", "-")
    except Exception:
        path = url

    slug = _SLUG_SAFE_RE.sub("-", path)
    slug = _MULTI_DASH_RE.sub("-", slug)
    slug = _LEADING_TRAILING_DASH_RE.sub("", slug)
    slug = slug[:max_length]
    slug = _LEADING_TRAILING_DASH_RE.sub("", slug)

    return slug or "index"


def is_non_content_url(url: str) -> bool:
    """Return True if the URL clearly points to a non-HTML asset."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    _, dot, ext = path.rpartition(".")
    return bool(dot) and f".{ext}" in NON_CONTENT_EXTENSIONS


def extract_domain(url: str) -> str:
    """Return the netloc (host) component of a URL, lowercased."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""
