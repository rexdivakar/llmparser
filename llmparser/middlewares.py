"""Scrapy downloader middlewares for llmparser.

Scrapy 2.14+ compatible: middleware methods do NOT take a `spider` argument.
Use `from_crawler()` to access spider/settings when needed.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapy import Request
    from scrapy.http import Response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User-agent pool (realistic browser strings)
# ---------------------------------------------------------------------------

_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]


class RotatingUserAgentMiddleware:
    """Assign a random browser User-Agent to every outgoing request."""

    def __init__(self, user_agents: list[str]) -> None:
        self.user_agents = user_agents or _USER_AGENTS

    @classmethod
    def from_crawler(cls, crawler: object) -> "RotatingUserAgentMiddleware":
        ua_list = getattr(crawler, "settings", {}).get("USER_AGENT_LIST", _USER_AGENTS)
        return cls(ua_list)

    def process_request(self, request: "Request") -> None:
        ua = random.choice(self.user_agents)
        request.headers["User-Agent"] = ua
        logger.debug("UA for %s: %s", request.url, ua)


# ---------------------------------------------------------------------------
# Playwright logging middleware
# ---------------------------------------------------------------------------

class PlaywrightLoggingMiddleware:
    """Lightweight middleware that logs which requests are rendered via Playwright.

    The actual Playwright rendering is handled by ScrapyPlaywrightDownloadHandler
    (configured conditionally in __main__.py when chromium is available).
    """

    def process_request(self, request: "Request") -> None:
        if request.meta.get("playwright"):
            logger.debug(
                "Playwright render: %s (retry=%s)",
                request.url,
                request.meta.get("playwright_retry", False),
            )

    def process_response(self, request: "Request", response: "Response") -> "Response":
        if request.meta.get("playwright"):
            logger.debug(
                "Playwright response: %s status=%s len=%d",
                response.url,
                response.status,
                len(response.body),
            )
        return response
