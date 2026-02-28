"""Per-domain rate limiting for network requests."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse


class DomainRateLimiter:
    """Simple per-domain limiter enforcing a minimum interval between requests."""

    def __init__(self, rate_per_domain: float) -> None:
        if rate_per_domain <= 0:
            raise ValueError("rate_per_domain must be > 0")
        self._min_interval = 1.0 / rate_per_domain
        self._lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}

    def wait(self, url: str) -> None:
        domain = urlparse(url).netloc.lower()
        if not domain:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last_request_at.get(domain, 0.0)
            next_allowed = max(now, last + self._min_interval)
            delay = next_allowed - now
            self._last_request_at[domain] = next_allowed
        if delay > 0:
            time.sleep(delay)
