"""llmparser.extractors.block_detection — Bot/block page detector.

Pure-function, no network calls.  Classifies a fetched HTML page as blocked
(CAPTCHA, Cloudflare challenge, DataDome, PerimeterX, Akamai, IP ban, soft
block, or empty) by fast string/regex matching.

Usage::

    from llmparser.extractors.block_detection import detect_block

    result = detect_block(html, url="https://example.com", status_code=200)
    if result.is_blocked:
        print(result.block_type, result.block_reason, result.confidence)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class BlockResult:
    """Result of a block-detection check."""

    is_blocked: bool
    # "captcha"|"cloudflare"|"datadome"|"perimeterx"|"akamai"|"ip_ban"|"soft_block"|"empty"
    block_type: str | None
    block_reason: str | None  # human-readable description
    confidence: float  # 0.0-1.0


# ---------------------------------------------------------------------------
# Compiled patterns (evaluated once at import time)
# ---------------------------------------------------------------------------

_CF_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"just a moment", re.IGNORECASE),
    re.compile(r"cf-browser-verification", re.IGNORECASE),
    re.compile(r"challenges\.cloudflare\.com", re.IGNORECASE),
    re.compile(r"cf-challenge", re.IGNORECASE),
    re.compile(r"__cf_bm", re.IGNORECASE),
    re.compile(r"cf-ray", re.IGNORECASE),
)

_CF_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"attention required", re.IGNORECASE),
    re.compile(r"just a moment", re.IGNORECASE),
)

_CAPTCHA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"g-recaptcha", re.IGNORECASE),
    re.compile(r"h-captcha", re.IGNORECASE),
    re.compile(r"hcaptcha\.com", re.IGNORECASE),
    re.compile(r"cf-turnstile", re.IGNORECASE),
    re.compile(r"FriendlyCaptcha", re.IGNORECASE),
    re.compile(r"recaptcha\.net", re.IGNORECASE),
)

_DATADOME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"datadome", re.IGNORECASE),
    re.compile(r"ddCaptcha", re.IGNORECASE),
    re.compile(r"_dd_s", re.IGNORECASE),
)

_PERIMETERX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"px-captcha", re.IGNORECASE),
    re.compile(r"pxi_loader", re.IGNORECASE),
    re.compile(r"_pxAppId", re.IGNORECASE),
    re.compile(r"perimeterx", re.IGNORECASE),
)

_AKAMAI_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ak_bmsc", re.IGNORECASE),
    re.compile(r"_abck", re.IGNORECASE),
    re.compile(r"bmak\.js", re.IGNORECASE),
)

# Matches external script src attributes (src="http..." or src='http...')
_EXTERNAL_SCRIPT_RE = re.compile(
    r"""<script[^>]+\bsrc\s*=\s*["']https?://""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_count(html: str) -> int:
    """Approximate visible word count by stripping tags via regex."""
    text = re.sub(r"<[^>]+>", " ", html)
    return len(text.split())


def _get_title(html: str) -> str:
    """Extract text content of the first <title> tag, or empty string."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _count_external_scripts(html: str) -> int:
    return len(_EXTERNAL_SCRIPT_RE.findall(html))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_block(
    html: str,
    *,
    url: str = "",
    status_code: int = 200,
) -> BlockResult:
    """Detect whether *html* is a bot-protection or block page.

    Checks are performed in priority order (cheapest / most specific first).

    Args:
        html:        Raw HTML string returned by the server.
        url:         Original request URL (informational only, not fetched).
        status_code: HTTP status code from the response (default 200).

    Returns:
        :class:`BlockResult` with ``is_blocked=False`` if no block is detected.
    """
    wc = _word_count(html)

    # ── Priority 1: IP ban (403/401/407 + sparse content) ────────────────────
    if status_code in (401, 403, 407) and wc < 200:
        origin = f" from {url}" if url else ""
        return BlockResult(
            is_blocked=True,
            block_type="ip_ban",
            block_reason=f"HTTP {status_code}{origin} with sparse content ({wc} words)",
            confidence=0.95,
        )

    # ── Priority 2: Cloudflare challenge ─────────────────────────────────────
    title = _get_title(html)
    cf_title_hit = any(p.search(title) for p in _CF_TITLE_PATTERNS)
    cf_body_hit = any(p.search(html) for p in _CF_BODY_PATTERNS)
    if cf_title_hit or cf_body_hit:
        return BlockResult(
            is_blocked=True,
            block_type="cloudflare",
            block_reason="Cloudflare challenge page detected",
            confidence=0.95,
        )

    # ── Priority 3: CAPTCHA ───────────────────────────────────────────────────
    captcha_hits = sum(1 for p in _CAPTCHA_PATTERNS if p.search(html))
    if captcha_hits >= 1:
        return BlockResult(
            is_blocked=True,
            block_type="captcha",
            block_reason=f"CAPTCHA widget detected ({captcha_hits} signal(s))",
            confidence=0.90,
        )

    # ── Priority 4: DataDome ─────────────────────────────────────────────────
    dd_hits = sum(1 for p in _DATADOME_PATTERNS if p.search(html))
    if dd_hits >= 1:
        return BlockResult(
            is_blocked=True,
            block_type="datadome",
            block_reason=f"DataDome bot protection detected ({dd_hits} signal(s))",
            confidence=0.92,
        )

    # ── Priority 5: PerimeterX ───────────────────────────────────────────────
    px_hits = sum(1 for p in _PERIMETERX_PATTERNS if p.search(html))
    if px_hits >= 1:
        return BlockResult(
            is_blocked=True,
            block_type="perimeterx",
            block_reason=f"PerimeterX bot protection detected ({px_hits} signal(s))",
            confidence=0.92,
        )

    # ── Priority 6: Akamai ───────────────────────────────────────────────────
    ak_hits = sum(1 for p in _AKAMAI_PATTERNS if p.search(html))
    if ak_hits >= 1:
        return BlockResult(
            is_blocked=True,
            block_type="akamai",
            block_reason=f"Akamai bot manager detected ({ak_hits} signal(s))",
            confidence=0.90,
        )

    # ── Priority 7: Soft block (sparse + heavy JS) ───────────────────────────
    ext_scripts = _count_external_scripts(html)
    if wc < 30 and ext_scripts > 6:
        return BlockResult(
            is_blocked=True,
            block_type="soft_block",
            block_reason=(
                f"Sparse content ({wc} words) with heavy JS load "
                f"({ext_scripts} external scripts)"
            ),
            confidence=0.75,
        )

    # ── Priority 8: Empty page (200 OK but nearly no content) ────────────────
    if status_code == 200 and wc < 20:
        return BlockResult(
            is_blocked=True,
            block_type="empty",
            block_reason=f"HTTP 200 but page has only {wc} words",
            confidence=0.80,
        )

    return BlockResult(is_blocked=False, block_type=None, block_reason=None, confidence=1.0)
