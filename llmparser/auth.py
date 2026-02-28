"""Authentication helpers for HTTP and Playwright fetchers."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        if not part.strip():
            continue
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


@dataclasses.dataclass
class AuthSession:
    """Container for authentication headers/cookies and optional refresh."""

    headers: dict[str, str] = dataclasses.field(default_factory=dict)
    cookies: dict[str, str] = dataclasses.field(default_factory=dict)
    bearer_token: str | None = None
    refresh: Callable[[], str | dict[str, str]] | None = None

    @classmethod
    def from_cookie_header(cls, cookie_header: str) -> AuthSession:
        return cls(cookies=_parse_cookie_header(cookie_header))

    def apply_headers(self, url: str, headers: dict[str, str]) -> None:
        if self.bearer_token:
            headers.setdefault("Authorization", f"Bearer {self.bearer_token}")
        if self.headers:
            headers.update(self.headers)
        if self.cookies and "Cookie" not in headers:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def playwright_cookies(self, url: str) -> list[dict[str, str]]:
        if not self.cookies:
            return []
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else url
        return [{"name": k, "value": v, "url": base_url} for k, v in self.cookies.items()]

    def refresh_if_needed(self) -> None:
        if not self.refresh:
            return
        try:
            updated = self.refresh()
        except Exception as exc:
            logger.warning("Auth refresh failed: %s", exc)
            return
        if isinstance(updated, str):
            self.bearer_token = updated
            return
        if isinstance(updated, dict):
            if "bearer_token" in updated:
                self.bearer_token = str(updated["bearer_token"])
            if "headers" in updated and isinstance(updated["headers"], dict):
                self.headers.update(updated["headers"])
            if "cookies" in updated and isinstance(updated["cookies"], dict):
                self.cookies.update(updated["cookies"])
