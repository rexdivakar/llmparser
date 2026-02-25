"""Scrapy extension: real-time Rich progress bar.

Enabled via the ``--progress`` CLI flag, which sets:
    PROGRESS_ENABLED = True
    LOG_LEVEL = WARNING   (silences Scrapy's chatty INFO output)

The progress bar runs in a background daemon thread so it never blocks
Twisted's event loop.  Stats are read from shared counters updated via
Scrapy signals.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)


class RichProgressExtension:
    """Live crawl progress bar rendered via Rich in a background thread."""

    def __init__(self, max_pages: int, enabled: bool) -> None:
        self._max_pages = max_pages
        self._enabled = enabled
        self._pages = 0
        self._articles = 0
        self._start = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "RichProgressExtension":
        from scrapy import signals

        ext = cls(
            max_pages=crawler.settings.getint("SPIDER_MAX_PAGES", 500),
            enabled=crawler.settings.getbool("PROGRESS_ENABLED", False),
        )
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.response_received, signal=signals.response_received)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    # ------------------------------------------------------------------
    # Signal handlers (run in Twisted thread)
    # ------------------------------------------------------------------

    def spider_opened(self, spider: object) -> None:
        if not self._enabled:
            return
        self._start = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_progress, daemon=True
        )
        self._thread.start()

    def response_received(self, response: object, request: object, spider: object) -> None:
        self._pages += 1

    def item_scraped(self, item: object, spider: object) -> None:
        self._articles += 1

    def spider_closed(self, spider: object, reason: str) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    # ------------------------------------------------------------------
    # Progress bar (background thread)
    # ------------------------------------------------------------------

    def _run_progress(self) -> None:
        try:
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
            )

            console = Console(stderr=True)

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]Crawling[/bold cyan]"),
                BarColumn(bar_width=28),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                TextColumn(
                    "[dim]{task.fields[rate]} p/s | "
                    "{task.fields[articles]} articles | "
                    "{task.fields[skipped]} skipped[/dim]"
                ),
                console=console,
                refresh_per_second=4,
                transient=False,
            ) as progress:
                task = progress.add_task(
                    "blog_spider",
                    total=self._max_pages or None,
                    rate="0.0",
                    articles="0",
                    skipped="0",
                )

                while not self._stop.wait(timeout=0.25):
                    elapsed = time.monotonic() - self._start
                    rate = self._pages / elapsed if elapsed > 0 else 0.0
                    skipped = max(0, self._pages - self._articles)
                    progress.update(
                        task,
                        completed=self._pages,
                        rate=f"{rate:.1f}",
                        articles=str(self._articles),
                        skipped=str(skipped),
                    )

                # Final update before exiting context
                elapsed = time.monotonic() - self._start
                rate = self._pages / elapsed if elapsed > 0 else 0.0
                progress.update(
                    task,
                    completed=self._pages,
                    rate=f"{rate:.1f}",
                    articles=str(self._articles),
                    skipped=str(max(0, self._pages - self._articles)),
                )

        except Exception as exc:
            logger.debug("RichProgressExtension: progress bar error: %s", exc)
