"""llmparser.plugins — Extension point registry for custom strategies, extractors,
scorers, and output formatters.

Usage::

    from llmparser import register_scorer

    class BoostTech:
        name = "boost_tech"
        def score(self, url: str, html: str, base_score: int) -> int:
            return base_score + (10 if "python" in html.lower() else 0)

    register_scorer(BoostTech())

All four plugin types follow ``runtime_checkable`` ``Protocol`` contracts so
you can use ``isinstance()`` checks in tests without inheriting from a base
class.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol definitions
# ---------------------------------------------------------------------------

@runtime_checkable
class FetchStrategyPlugin(Protocol):
    """Custom HTTP fetch strategy invoked after all built-in strategies fail."""

    name: str

    def can_handle(self, url: str, signals: Any) -> bool:
        """Return True if this plugin should attempt to fetch *url*."""
        ...

    def fetch(self, url: str, timeout: int) -> str:
        """Fetch *url* and return the HTML string."""
        ...


@runtime_checkable
class ExtractorPlugin(Protocol):
    """Custom main-content extractor, tried after the built-in cascade."""

    name: str
    priority: int  # Higher = tried first among registered plugins

    def can_extract(self, html: str, url: str) -> bool:
        """Return True if this plugin can extract content from *html*."""
        ...

    def extract(self, html: str, url: str) -> str | None:
        """Return an HTML fragment with the main content, or None to skip."""
        ...


@runtime_checkable
class ScorerPlugin(Protocol):
    """Adjusts the article score returned by the built-in heuristic scorer."""

    name: str

    def score(self, url: str, html: str, base_score: int) -> int:
        """Return a new score (may be higher or lower than *base_score*)."""
        ...


@runtime_checkable
class OutputFormatterPlugin(Protocol):
    """Writes a custom output file alongside the default .json / .md files."""

    name: str
    extension: str  # File extension, e.g. "txt" or "rst" (no leading dot)

    def format(self, article: Any) -> str:
        """Return the formatted text to write to ``<slug>.<extension>``."""
        ...


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_registry: dict[str, list[Any]] = {
    "strategies": [],
    "extractors": [],
    "scorers": [],
    "formatters": [],
}


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

def register_strategy(plugin: FetchStrategyPlugin) -> None:
    """Register a custom :class:`FetchStrategyPlugin`."""
    _registry["strategies"].append(plugin)


def register_extractor(plugin: ExtractorPlugin) -> None:
    """Register a custom :class:`ExtractorPlugin`."""
    _registry["extractors"].append(plugin)


def register_scorer(plugin: ScorerPlugin) -> None:
    """Register a custom :class:`ScorerPlugin`."""
    _registry["scorers"].append(plugin)


def register_formatter(plugin: OutputFormatterPlugin) -> None:
    """Register a custom :class:`OutputFormatterPlugin`."""
    _registry["formatters"].append(plugin)


# ---------------------------------------------------------------------------
# Accessor helpers
# ---------------------------------------------------------------------------

def get_strategies() -> list[FetchStrategyPlugin]:
    """Return all registered fetch-strategy plugins."""
    return list(_registry["strategies"])


def get_extractors() -> list[ExtractorPlugin]:
    """Return all registered extractor plugins."""
    return list(_registry["extractors"])


def get_scorers() -> list[ScorerPlugin]:
    """Return all registered scorer plugins."""
    return list(_registry["scorers"])


def get_formatters() -> list[OutputFormatterPlugin]:
    """Return all registered formatter plugins."""
    return list(_registry["formatters"])


def clear_plugins() -> None:
    """Remove all registered plugins. Primarily for use in tests."""
    for value in _registry.values():
        value.clear()
