"""RSS 2.0 / Atom 1.0 feed parser.

Returns a list of FeedEntry namedtuples (url, title, author, published_at,
summary) without making any network requests.  Network I/O is handled by the
caller (query.fetch_feed).

Supports:
  - RSS 2.0 (<rss> root, <channel>/<item> structure)
  - Atom 1.0 (<feed xmlns="http://www.w3.org/2005/Atom"> root, <entry> elements)
  - Dublin Core namespace for author/date in RSS
  - Graceful fallback when neither format is detected
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET  # for ET.ParseError only
from typing import NamedTuple
from urllib.parse import urljoin

import defusedxml.ElementTree as defused_ET

logger = logging.getLogger(__name__)

_ATOM_NS = "http://www.w3.org/2005/Atom"
_DC_NS = "http://purl.org/dc/elements/1.1/"


class FeedEntry(NamedTuple):
    """Single article entry from an RSS or Atom feed."""

    url: str
    title: str
    author: str | None
    published_at: str | None
    summary: str | None


def _text(el: ET.Element | None) -> str | None:
    """Return stripped element text or None."""
    if el is None:
        return None
    t = (el.text or "").strip()
    return t or None


def _parse_rss(root: ET.Element) -> list[FeedEntry]:
    """Parse RSS 2.0 <channel>/<item> structure."""
    channel = root.find("channel")
    items = (channel if channel is not None else root).findall("item")
    entries: list[FeedEntry] = []

    for item in items:
        # <link> in RSS is plain text, not an attribute
        link_el = item.find("link")
        url = _text(link_el)
        if not url:
            # Some RSS feeds use <guid isPermaLink="true">
            guid_el = item.find("guid")
            if guid_el is not None:
                is_link = (guid_el.get("isPermaLink", "true")).lower() != "false"
                if is_link:
                    url = _text(guid_el)
        if not url:
            continue

        title = _text(item.find("title")) or ""
        author = (
            _text(item.find(f"{{{_DC_NS}}}creator"))
            or _text(item.find("author"))
        )
        published_at = (
            _text(item.find("pubDate"))
            or _text(item.find(f"{{{_DC_NS}}}date"))
        )
        # Description may contain HTML — store as-is for summary
        summary = _text(item.find("description"))

        entries.append(
            FeedEntry(
                url=url,
                title=title,
                author=author,
                published_at=published_at,
                summary=summary,
            ),
        )

    return entries


def _parse_atom(root: ET.Element, base_url: str) -> list[FeedEntry]:
    """Parse Atom 1.0 <feed>/<entry> structure (with or without namespace)."""
    # Root tag may be "{http://www.w3.org/2005/Atom}feed" or plain "feed"
    ns = _ATOM_NS if root.tag.startswith("{") else ""
    pfx = f"{{{ns}}}" if ns else ""

    entries: list[FeedEntry] = []
    for entry in root.findall(f"{pfx}entry"):
        # Find the canonical alternate link
        url: str | None = None
        for link_el in entry.findall(f"{pfx}link"):
            rel = link_el.get("rel", "alternate")
            if rel in ("alternate", ""):
                href = link_el.get("href", "").strip()
                if href:
                    url = urljoin(base_url, href) if base_url else href
                    break
        if not url:
            continue

        title_el = entry.find(f"{pfx}title")
        title = _text(title_el) or ""

        author_el = entry.find(f"{pfx}author")
        author: str | None = None
        if author_el is not None:
            author = _text(author_el.find(f"{pfx}name"))

        pub_el = entry.find(f"{pfx}published") or entry.find(f"{pfx}updated")
        published_at = _text(pub_el)

        summary_el = entry.find(f"{pfx}summary") or entry.find(f"{pfx}content")
        summary = _text(summary_el)

        entries.append(
            FeedEntry(
                url=url,
                title=title,
                author=author,
                published_at=published_at,
                summary=summary,
            ),
        )

    return entries


def parse_feed(xml_text: str, base_url: str = "") -> list[FeedEntry]:
    """Parse RSS 2.0 or Atom 1.0 XML and return a list of :class:`FeedEntry`.

    Detects the feed format automatically from the root element tag.
    Returns an empty list on parse failure rather than raising.

    Args:
        xml_text: Raw XML string of the feed.
        base_url: Base URL used to resolve relative Atom entry links.

    Returns:
        Ordered list of :class:`FeedEntry` instances, newest first if the
        feed is ordered (no re-sorting is applied).
    """
    try:
        root = defused_ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Feed XML parse error: %s", exc)
        return []

    tag = root.tag.lower()

    # RSS: root is <rss> or root contains <channel>
    if "rss" in tag or root.find("channel") is not None:
        entries = _parse_rss(root)
        if entries:
            return entries
        # Fall through and try Atom in case of unusual structure

    # Atom: root is <feed> (with or without namespace)
    if "feed" in tag or f"{{{_ATOM_NS}}}feed" == root.tag:
        return _parse_atom(root, base_url)

    # Unknown — try RSS then Atom
    entries = _parse_rss(root)
    if not entries:
        entries = _parse_atom(root, base_url)
    if not entries:
        logger.warning("Could not detect feed format for root tag: %s", root.tag)
    return entries
