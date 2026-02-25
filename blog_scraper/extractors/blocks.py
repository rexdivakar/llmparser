"""Convert extracted HTML into structured content blocks.

Block types: heading | paragraph | image | code | list | quote | table
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

# Heading tags → level number
_HEADING_LEVELS: dict[str, int] = {f"h{i}": i for i in range(1, 7)}

# Block-level elements to process
_BLOCK_TAGS = frozenset(
    {
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p",
        "img",
        "figure",
        "pre",
        "ul", "ol",
        "blockquote",
        "table",
    }
)

# Elements that should not be traversed into directly (handled as a unit)
_LEAF_CONTAINERS = frozenset({"pre", "table", "ul", "ol", "blockquote"})

_LANG_CLASS_RE = re.compile(r"language-(\w+)")

def _extract_code_language(tag: Tag) -> str:
    """Detect language from class="language-X" on <pre> or its <code> child."""
    for el in [tag, tag.find("code")]:
        if not el or not isinstance(el, Tag):
            continue
        for cls in el.get("class") or []:
            m = _LANG_CLASS_RE.match(str(cls))
            if m:
                return m.group(1)
    return ""


def _table_to_rows(table_tag: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table_tag.find_all("tr"):
        cells = [td.get_text().strip() for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


def _image_block(img: Tag, base_url: str) -> dict:
    src = str(img.get("src") or "").strip()
    if not src:
        srcset = str(img.get("srcset") or "").strip()
        if srcset:
            src = srcset.split(",")[0].strip().split(" ")[0]
    if src and base_url:
        src = urljoin(base_url, src)
    alt = str(img.get("alt") or "").strip()
    caption = ""
    # Look for <figcaption> in parent <figure>
    parent = img.parent
    if isinstance(parent, Tag) and parent.name == "figure":
        figcaption = parent.find("figcaption")
        if figcaption:
            caption = figcaption.get_text().strip()
    return {"type": "image", "url": src, "alt": alt, "caption": caption}


def _process_element(el: Tag, base_url: str, blocks: list[dict]) -> None:
    """Recursively process *el* and append blocks."""
    tag_name = el.name

    # Headings
    if tag_name in _HEADING_LEVELS:
        text = el.get_text().strip()
        if text:
            blocks.append({"type": "heading", "level": _HEADING_LEVELS[tag_name], "text": text})
        return

    # Paragraphs
    if tag_name == "p":
        # Check if para contains only an image
        imgs = el.find_all("img")
        non_img_text = el.get_text().strip()
        if imgs and not non_img_text:
            for img in imgs:
                if isinstance(img, Tag):
                    blocks.append(_image_block(img, base_url))
            return
        text = el.get_text().strip()
        if text:
            blocks.append({"type": "paragraph", "text": text})
        return

    # Standalone image or figure
    if tag_name == "img":
        blocks.append(_image_block(el, base_url))
        return
    if tag_name == "figure":
        img = el.find("img")
        if img and isinstance(img, Tag):
            blocks.append(_image_block(img, base_url))
        return

    # Code blocks (<pre> wrapping <code>)
    if tag_name == "pre":
        lang = _extract_code_language(el)
        code_el = el.find("code")
        text = (code_el if code_el else el).get_text()
        blocks.append({"type": "code", "language": lang, "text": text})
        return

    # Lists
    if tag_name in ("ul", "ol"):
        items = [li.get_text().strip() for li in el.find_all("li", recursive=False)]
        # Also handle nested items that aren't direct children
        if not items:
            items = [li.get_text().strip() for li in el.find_all("li")]
        items = [i for i in items if i]
        if items:
            blocks.append({"type": "list", "ordered": tag_name == "ol", "items": items})
        return

    # Block quotes
    if tag_name == "blockquote":
        text = el.get_text().strip()
        if text:
            blocks.append({"type": "quote", "text": text})
        return

    # Tables
    if tag_name == "table":
        rows = _table_to_rows(el)
        if rows:
            blocks.append({"type": "table", "rows": rows})
        return


def html_to_blocks(html: str, base_url: str = "") -> list[dict]:
    """Parse *html* into a flat list of typed content blocks.

    Traverses the DOM and emits blocks for headings, paragraphs, images,
    code, lists, block quotes, and tables.  Does NOT recurse into
    *_LEAF_CONTAINERS* (they are emitted as atomic blocks).
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    # Remove boilerplate
    for tag_name in ("nav", "header", "footer", "script", "style", "noscript"):
        for el in soup.find_all(tag_name):
            el.decompose()

    blocks: list[dict] = []
    body = soup.find("body") or soup

    def _walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            tag = child.name
            if tag in _BLOCK_TAGS:
                _process_element(child, base_url, blocks)
            elif tag not in _LEAF_CONTAINERS:
                # Recurse into containers (div, section, article, etc.)
                _walk(child)

    _walk(body)
    return blocks
