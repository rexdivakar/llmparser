"""Extraction sub-package: deterministic, template-agnostic content extraction."""

from .metadata import extract_metadata
from .main_content import extract_main_content
from .blocks import html_to_blocks
from .markdown import html_to_markdown
from .urlnorm import normalize_url, url_to_slug
from .heuristics import Heuristics

__all__ = [
    "extract_metadata",
    "extract_main_content",
    "html_to_blocks",
    "html_to_markdown",
    "normalize_url",
    "url_to_slug",
    "Heuristics",
]
