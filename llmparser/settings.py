"""Scrapy project settings for llmparser.

Scrapy 2.14+ compatibility notes:
- spider argument removed from middleware/pipeline methods
- CONCURRENT_REQUESTS_PER_IP deprecated (use CONCURRENT_REQUESTS_PER_DOMAIN)
- Playwright handlers configured lazily in __main__.py to avoid startup errors
  when chromium is not installed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Project identity
# ---------------------------------------------------------------------------
BOT_NAME = "llmparser"

SPIDER_MODULES = ["spiders"]
NEWSPIDER_MODULE = "spiders"

# ---------------------------------------------------------------------------
# Crawl politeness
# ---------------------------------------------------------------------------
ROBOTSTXT_OBEY = True

# Base download delay (seconds). AutoThrottle adjusts dynamically.
DOWNLOAD_DELAY = 1.0

CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 4

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 60.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False

DOWNLOAD_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# ---------------------------------------------------------------------------
# HTTP cache (disabled by default; enable for development)
# ---------------------------------------------------------------------------
HTTPCACHE_ENABLED = False
HTTPCACHE_EXPIRATION_SECS = 86400
HTTPCACHE_DIR = ".scrapy/httpcache"
HTTPCACHE_IGNORE_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# ---------------------------------------------------------------------------
# User-agent
# ---------------------------------------------------------------------------
# Overridden per-request by RotatingUserAgentMiddleware
USER_AGENT = "LLMParser/0.2 (+https://github.com/user/llmparser)"

# ---------------------------------------------------------------------------
# Feeds (unused - we write output ourselves in pipelines)
# ---------------------------------------------------------------------------
FEEDS: dict = {}

# ---------------------------------------------------------------------------
# Downloader middlewares
# ---------------------------------------------------------------------------
DOWNLOADER_MIDDLEWARES: dict[str, int | None] = {
    # Disable the default UA middleware; ours rotates
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    "llmparser.middlewares.RotatingUserAgentMiddleware": 400,
    # Retry middleware (keep default priority)
    "scrapy.downloadermiddlewares.retry.RetryMiddleware": 550,
    # Playwright logging (lightweight; actual rendering via download handler)
    "llmparser.middlewares.PlaywrightLoggingMiddleware": 650,
}

# ---------------------------------------------------------------------------
# Item pipelines
# ---------------------------------------------------------------------------
ITEM_PIPELINES: dict[str, int] = {
    # 150: drop exact-duplicate content before validation
    "llmparser.pipelines.ContentHashDedupPipeline": 150,
    "llmparser.pipelines.ArticleValidationPipeline": 200,
    "llmparser.pipelines.ArticleWriterPipeline": 300,
    "llmparser.pipelines.IndexWriterPipeline": 400,
}

# ---------------------------------------------------------------------------
# Scrapy extensions
# ---------------------------------------------------------------------------
EXTENSIONS: dict[str, int] = {
    "llmparser.extensions.RichProgressExtension": 500,
}

# ---------------------------------------------------------------------------
# Progress bar (toggled by --progress CLI flag)
# ---------------------------------------------------------------------------
PROGRESS_ENABLED = False
SPIDER_MAX_PAGES = 500  # kept in sync with --max-pages via __main__.py

# ---------------------------------------------------------------------------
# Output directory (override via CLI --out or spider argument)
# ---------------------------------------------------------------------------
OUTPUT_DIR = "./out"

# ---------------------------------------------------------------------------
# Scrapy-Playwright (JS rendering)
# ---------------------------------------------------------------------------
# DOWNLOAD_HANDLERS and TWISTED_REACTOR are intentionally NOT set here.
# They are added programmatically in __main__.py only when:
#   - render_js != 'never'
#   - scrapy-playwright package is installed
#   - Playwright chromium browser is installed
#
# This prevents startup failures when chromium is not available.
# The Playwright handler launches the browser on first open(), which
# fails immediately if chromium is not installed.

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS: dict = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000  # ms

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# ---------------------------------------------------------------------------
# Request fingerprinting
# ---------------------------------------------------------------------------
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
TELNETCONSOLE_ENABLED = False
