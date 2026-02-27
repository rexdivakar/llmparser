"""llmparser.proxy — Proxy configuration and rotation helpers.

Usage::

    from llmparser.proxy import ProxyConfig, ProxyRotator

    config = ProxyConfig(
        proxies=["http://p1:8080", "http://user:pass@p2:8080"],
        rotation="round_robin",
    )
    rotator = ProxyRotator(config)

    proxy = rotator.get_proxy()          # current proxy
    rotator.mark_failed(proxy)           # consecutive failure tracking
    next_proxy = rotator.rotate()        # advance and return next
    print(rotator.has_proxies())         # False when all exhausted
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class ProxyConfig:
    """Configuration for proxy rotation.

    Attributes:
        proxies:  List of proxy URLs.  Supports plain ``http://host:port``
                  and authenticated ``http://user:pass@host:port`` forms.
        rotation: Rotation strategy.  ``"round_robin"`` (default) cycles
                  through the list sequentially; ``"random"`` picks uniformly
                  at random on each call to :meth:`ProxyRotator.rotate`.
    """

    proxies: list[str]
    rotation: str = "round_robin"  # "round_robin" | "random"


# Maximum consecutive failures before a proxy is skipped
_MAX_FAILURES = 3


class ProxyRotator:
    """Manages proxy selection and failure tracking for a scraping session.

    A proxy is considered *exhausted* after :data:`_MAX_FAILURES` consecutive
    failures.  Once all proxies are exhausted, :meth:`has_proxies` returns
    ``False`` and :meth:`get_proxy` / :meth:`rotate` return ``None``.
    """

    def __init__(self, config: ProxyConfig) -> None:
        if config.rotation not in ("round_robin", "random"):
            raise ValueError(
                f"rotation must be 'round_robin' or 'random'; got {config.rotation!r}",
            )
        self._proxies: list[str] = list(config.proxies)
        self._rotation: str = config.rotation
        self._index: int = 0
        # consecutive failure count per proxy URL
        self._failures: dict[str, int] = {p: 0 for p in self._proxies}
        # per-proxy exhaustion flag (set permanently when failures >= _MAX_FAILURES)
        self._exhausted: dict[str, bool] = {p: False for p in self._proxies}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_proxies(self) -> list[str]:
        """Return list of proxies that have not yet been exhausted."""
        return [p for p in self._proxies if not self._exhausted[p]]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_proxy(self) -> str | None:
        """Return the currently selected proxy, or ``None`` if all are exhausted."""
        active = self._active_proxies()
        if not active:
            return None
        if self._rotation == "random":
            return random.choice(active)
        # round_robin: use _index, clamped to active pool
        idx = self._index % len(active)
        return active[idx]

    def rotate(self) -> str | None:
        """Advance to the next proxy and return it.

        For ``round_robin``: moves the internal cursor forward by one.
        For ``random``: picks a new random proxy from the active pool.

        Returns ``None`` when no more proxies are available.
        """
        active = self._active_proxies()
        if not active:
            return None
        if self._rotation == "random":
            return random.choice(active)
        self._index = (self._index + 1) % len(active)
        return active[self._index % len(active)]

    def mark_failed(self, proxy: str) -> None:
        """Record a consecutive failure for *proxy*.

        After :data:`_MAX_FAILURES` consecutive failures the proxy is marked
        exhausted and will be skipped by :meth:`get_proxy` and :meth:`rotate`.
        """
        if proxy not in self._failures:
            return
        self._failures[proxy] += 1
        if self._failures[proxy] >= _MAX_FAILURES:
            self._exhausted[proxy] = True

    def mark_success(self, proxy: str) -> None:
        """Reset the consecutive failure counter for *proxy* after a success."""
        if proxy in self._failures:
            self._failures[proxy] = 0

    def has_proxies(self) -> bool:
        """Return ``True`` while at least one proxy is still active."""
        return bool(self._active_proxies())
