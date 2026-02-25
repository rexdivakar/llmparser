"""Convert HTML to Markdown, preserving code blocks, tables, and structure."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def html_to_markdown(html: str) -> str:
    """Convert *html* to clean Markdown.

    Uses markdownify with ATX heading style.  Post-processes to:
    - Remove excessive blank lines (>2 consecutive)
    - Strip trailing whitespace from lines
    - Ensure code fences use triple backticks
    """
    if not html or not html.strip():
        return ""

    try:
        from markdownify import markdownify  # type: ignore[import-untyped]

        md = markdownify(
            html,
            heading_style="ATX",
            bullets="-",
            code_language_callback=_detect_lang,
            strip=["script", "style", "nav", "header", "footer"],
        )
    except Exception:
        # Graceful fallback: strip tags and return plain text
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        md = soup.get_text(separator="\n")

    # Post-process
    md = _TRAILING_WHITESPACE_RE.sub("", md)
    md = _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", md)
    return md.strip()


def _detect_lang(el: object) -> str:
    """Extract language hint from an element's class list for markdownify."""
    try:
        # el is a BeautifulSoup Tag
        getter = getattr(el, "get", None)
        classes = (getter("class") if getter else None) or []
        for cls in classes:
            if isinstance(cls, str) and cls.startswith("language-"):
                return cls[len("language-"):]
    except Exception as exc:
        logger.debug("Language detection failed for element: %s", exc)
    return ""


def format_markdown_article(
    title: str,
    author: str | None,
    published_at: str | None,
    tags: list[str],
    summary: str | None,
    content_markdown: str,
) -> str:
    """Render a complete article Markdown document with front-matter header."""
    lines: list[str] = []

    lines.append(f"# {title}")
    lines.append("")

    meta_parts: list[str] = []
    if author:
        meta_parts.append(f"**Author:** {author}")
    if published_at:
        meta_parts.append(f"**Published:** {published_at}")
    if tags:
        meta_parts.append(f"**Tags:** {', '.join(tags)}")

    if meta_parts:
        lines.extend(meta_parts)
        lines.append("")

    if summary:
        lines.append(f"> {summary}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(content_markdown)

    return "\n".join(lines)
