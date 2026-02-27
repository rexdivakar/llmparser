"""CLI entry point: python -m llmparser --url URL [options]"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Tell Scrapy which settings module to use (read during Settings() init).
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "llmparser.settings")

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llmparser",
        description=(
            "Parse any website and extract structured, LLM-ready content.\n"
            "Adaptive engine — static, Playwright, AMP, RSS. No LLMs required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", required=True, metavar="URL",
                        help="Blog root URL or any article URL to start crawling from")
    parser.add_argument("--out", default="./out", metavar="DIR",
                        help="Output directory (default: ./out)")
    parser.add_argument("--max-pages", type=int, default=500, metavar="N",
                        help="Maximum pages to scrape (default: 500)")
    parser.add_argument("--max-depth", type=int, default=10, metavar="N",
                        help="Maximum BFS crawl depth (default: 10)")
    parser.add_argument("--concurrency", type=int, default=8, metavar="N",
                        help="Concurrent requests (default: 8)")
    parser.add_argument("--render-js", choices=["auto", "always", "never"],
                        default="auto", metavar="{auto,always,never}",
                        help="JavaScript rendering mode (default: auto)")
    parser.add_argument("--ignore-robots", action="store_true", default=False,
                        help="Ignore robots.txt (default: obey)")
    parser.add_argument("--include-regex", default=None, metavar="PATTERN",
                        help="Only crawl URLs matching this regex")
    parser.add_argument("--exclude-regex", default=None, metavar="PATTERN",
                        help="Skip URLs matching this regex")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        metavar="{DEBUG,INFO,WARNING,ERROR}",
                        help="Logging level (default: INFO)")
    # --- new flags ---
    parser.add_argument("--cache", action="store_true", default=False,
                        help="Enable HTTP response cache for faster re-runs (default: off)")
    parser.add_argument("--resume", action="store_true", default=False,
                        help="Resume a previous crawl: skip URLs already in seen_urls.txt")
    parser.add_argument("--allow-subdomains", action="store_true", default=False,
                        help="Crawl subdomains of the start URL's domain (e.g. docs.example.com)")
    parser.add_argument("--extra-domains", default=None, metavar="DOMAINS",
                        help=(
                            "Comma-separated extra domains to crawl "
                            "(e.g. 'blog.example.com,news.example.com')"
                        ))
    parser.add_argument("--progress", action="store_true", default=False,
                        help="Show a live Rich progress bar (sets log-level to WARNING)")
    return parser


def _validate_regex_args(args: argparse.Namespace) -> str | None:
    """Return an error message if any regex flags are invalid, else None."""
    for flag, pattern in [
        ("--include-regex", args.include_regex),
        ("--exclude-regex", args.exclude_regex),
    ]:
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                return f"Invalid {flag} pattern {pattern!r}: {exc}"
    return None


def _check_playwright_available() -> bool:
    """Return True if scrapy-playwright and chromium are both usable."""
    try:
        import scrapy_playwright  # noqa: F401
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # Check if chromium executable exists
            browser_type = p.chromium
            if not browser_type.executable_path:
                return False
    except Exception:
        return False
    else:
        return True


def _configure_playwright(scrapy_settings: Any) -> bool:
    """Add Playwright download handlers to *scrapy_settings* if available.

    Returns True if successfully configured, False if Playwright is unavailable.
    """
    if not _check_playwright_available():
        return False
    try:
        scrapy_settings.set(
            "DOWNLOAD_HANDLERS",
            {
                "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            },
            priority="cmdline",
        )
        scrapy_settings.set(
            "TWISTED_REACTOR",
            "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
            priority="cmdline",
        )
    except Exception:
        return False
    else:
        return True


def _print_banner(args: argparse.Namespace) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(
            Panel.fit(
                f"[bold cyan]LLMParser[/bold cyan]\n"
                f"URL:            [green]{args.url}[/green]\n"
                f"Output:         [yellow]{args.out}[/yellow]\n"
                f"Max pages:      {args.max_pages}\n"
                f"Max depth:      {args.max_depth}\n"
                f"Concurrency:    {args.concurrency}\n"
                f"Render JS:      {args.render_js}\n"
                f"Robots.txt:     {'ignore' if args.ignore_robots else 'obey'}\n"
                f"HTTP cache:     {'on' if args.cache else 'off'}\n"
                f"Resume:         {'yes' if args.resume else 'no'}\n"
                f"Subdomains:     {'allow' if args.allow_subdomains else 'block'}\n"
                f"Extra domains:  {args.extra_domains or '—'}",
                border_style="cyan",
                title="[bold]Configuration[/bold]",
            ),
        )
    except ImportError:
        print(f"LLMParser | URL: {args.url} | Out: {args.out}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --- #8 Validate regex flags early, before any heavy imports ---
    regex_err = _validate_regex_args(args)
    if regex_err:
        print(f"ERROR: {regex_err}", file=sys.stderr)
        return 1

    _print_banner(args)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # --progress silences Scrapy's chatty output so the bar is readable
    effective_log_level = "WARNING" if args.progress else args.log_level
    # NOTE: do NOT call logging.basicConfig() here — CrawlerProcess installs
    # Scrapy's own handler on the root logger.  A second basicConfig() call
    # would add a duplicate StreamHandler, printing every line twice.

    try:
        from scrapy.settings import Settings

        from llmparser import settings as settings_module
    except ImportError as exc:
        print(f"ERROR: Could not import Scrapy: {exc}", file=sys.stderr)
        print("Run: pip install -e .", file=sys.stderr)
        return 1

    scrapy_settings = Settings()
    scrapy_settings.setmodule(settings_module, priority="project")

    # Core CLI overrides
    scrapy_settings.set("CONCURRENT_REQUESTS", args.concurrency, priority="cmdline")
    scrapy_settings.set("LOG_LEVEL", effective_log_level, priority="cmdline")
    scrapy_settings.set("ROBOTSTXT_OBEY", not args.ignore_robots, priority="cmdline")
    scrapy_settings.set("OUTPUT_DIR", str(out_dir), priority="cmdline")
    scrapy_settings.set("SPIDER_MAX_PAGES", args.max_pages, priority="cmdline")

    # --- #7 HTTP cache ---
    if args.cache:
        scrapy_settings.set("HTTPCACHE_ENABLED", True, priority="cmdline")
        scrapy_settings.set(
            "HTTPCACHE_DIR", str(out_dir / ".httpcache"), priority="cmdline",
        )
        logger.info("HTTP cache enabled -> %s", out_dir / ".httpcache")

    # --- #6 Live progress bar ---
    if args.progress:
        scrapy_settings.set("PROGRESS_ENABLED", True, priority="cmdline")

    # Conditionally enable Playwright
    playwright_enabled = False
    if args.render_js != "never":
        playwright_enabled = _configure_playwright(scrapy_settings)
        if not playwright_enabled and args.render_js == "always":
            print(
                "WARNING: --render-js always requested but Playwright/chromium is not "
                "installed. Run: playwright install chromium\n"
                "Falling back to static HTML extraction.",
                file=sys.stderr,
            )
        elif not playwright_enabled:
            logger.info(
                "Playwright not available; JS rendering disabled. "
                "Install with: playwright install chromium",
            )

    try:
        from scrapy.crawler import CrawlerProcess

        process = CrawlerProcess(scrapy_settings)
        process.crawl(
            "blog_spider",
            start_url=args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            render_js=args.render_js if playwright_enabled else "never",
            include_regex=args.include_regex,
            exclude_regex=args.exclude_regex,
            out_dir=str(out_dir),
            # --- new spider args ---
            allow_subdomains=args.allow_subdomains,      # #3
            extra_domains=args.extra_domains,             # #3
            resume=args.resume,                           # #2
        )
        process.start()
    except Exception:
        logger.exception("Scraper failed")
        return 1

    _print_summary(out_dir)
    _write_summary_txt(out_dir)
    return 0


def _load_index(out_dir: Path) -> list[dict]:
    index_path = out_dir / "index.json"
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_skipped(out_dir: Path) -> list[dict]:
    skipped_path = out_dir / "skipped.jsonl"
    if not skipped_path.exists():
        return []
    entries: list[dict] = []
    try:
        for raw_line in skipped_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped:
                with contextlib.suppress(Exception):
                    entries.append(json.loads(stripped))
    except Exception as exc:
        logger.debug("Failed to read skipped.jsonl: %s", exc)
    return entries


def _print_summary(out_dir: Path) -> None:
    try:
        from rich import box
        from rich.console import Console
        from rich.rule import Rule
        from rich.table import Table

        console = Console()
        articles = _load_index(out_dir)
        raw_skipped = _load_skipped(out_dir)

        # Deduplicate skipped by url+reason
        _seen_skip: set[str] = set()
        skipped: list[dict] = []
        for s in raw_skipped:
            k = f"{s.get('url','')}|{s.get('reason','')}"
            if k not in _seen_skip:
                _seen_skip.add(k)
                skipped.append(s)

        total_words = sum(e.get("word_count", 0) for e in articles)
        total_read  = sum(e.get("reading_time_minutes", 0) for e in articles)

        # ── Stats panel ─────────────────────────────────────────────────────
        console.print()
        console.print(Rule("[bold cyan]Crawl Summary[/bold cyan]"))
        console.print(f"  [bold]Articles extracted :[/bold] [green]{len(articles)}[/green]")
        console.print(f"  [bold]Pages skipped      :[/bold] [yellow]{len(skipped)}[/yellow]")
        console.print(f"  [bold]Total words        :[/bold] {total_words:,}")
        console.print(f"  [bold]Total reading time :[/bold] {total_read} min")
        console.print(f"  [bold]Output directory   :[/bold] [green]{out_dir}[/green]")
        summary_file = out_dir / "summary.txt"
        console.print(
            f"  [bold]Summary file       :[/bold] [green]{summary_file}[/green]",
        )
        console.print()

        # ── Articles table ───────────────────────────────────────────────────
        if articles:
            tbl = Table(
                title=f"[bold green]Successfully Extracted Articles ({len(articles)})[/bold green]",
                box=box.SIMPLE_HEAVY,
                show_lines=False,
            )
            tbl.add_column("#",       style="dim",    justify="right", width=4,  no_wrap=True)
            tbl.add_column("Title",   style="cyan",   max_width=48,             no_wrap=True)
            tbl.add_column("Author",  style="green",  max_width=18,             no_wrap=True)
            tbl.add_column("Published", style="yellow", width=12,               no_wrap=True)
            tbl.add_column("Words",   justify="right", width=7,                 no_wrap=True)
            tbl.add_column("Read",    justify="right", width=6,                 no_wrap=True)
            tbl.add_column("Method",  style="dim",    width=12,                 no_wrap=True)
            tbl.add_column("URL",     style="blue",   max_width=50,             no_wrap=True)

            for i, e in enumerate(articles, 1):
                tbl.add_row(
                    str(i),
                    (e.get("title") or "-")[:45],
                    (e.get("author") or "-")[:18],
                    (e.get("published_at") or "-")[:10],
                    str(e.get("word_count", 0)),
                    f"{e.get('reading_time_minutes', 0)} min",
                    e.get("extraction_method_used", "-")[:12],
                    e.get("url", "")[:45],
                )
            console.print(tbl)

        # ── Skipped table ────────────────────────────────────────────────────
        if skipped:
            stbl = Table(
                title=f"[bold yellow]Skipped / Failed Pages ({len(skipped)})[/bold yellow]",
                box=box.SIMPLE_HEAVY,
                show_lines=False,
            )
            stbl.add_column("#",      style="dim",    justify="right", width=4,  no_wrap=True)
            stbl.add_column("URL",    style="blue",   max_width=65,             no_wrap=True)
            stbl.add_column("Reason", style="red",    max_width=45,             no_wrap=True)

            for i, s in enumerate(skipped, 1):
                stbl.add_row(
                    str(i),
                    s.get("url", "-")[:60],
                    s.get("reason", "-")[:40],
                )
            console.print(stbl)

    except Exception as exc:
        logger.debug("Rich summary display failed: %s", exc)


def _write_summary_txt(out_dir: Path) -> None:
    """Write a plain-text summary.txt to *out_dir* after the crawl."""
    articles = _load_index(out_dir)
    skipped  = _load_skipped(out_dir)

    # Deduplicate skipped entries
    seen: set[str] = set()
    deduped_skipped: list[dict] = []
    for s in skipped:
        key = f"{s.get('url','')}|{s.get('reason','')}"
        if key not in seen:
            seen.add(key)
            deduped_skipped.append(s)

    total_words = sum(e.get("word_count", 0) for e in articles)
    total_read  = sum(e.get("reading_time_minutes", 0) for e in articles)
    now         = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = []

    def h1(text: str) -> None:
        lines.append("")
        lines.append("=" * 72)
        lines.append(f"  {text}")
        lines.append("=" * 72)

    def h2(text: str) -> None:
        lines.append("")
        lines.append(f"--- {text} " + "-" * max(0, 68 - len(text)))

    # Header
    lines.append("=" * 72)
    lines.append("  BLOG SCRAPER — CRAWL SUMMARY REPORT")
    lines.append("=" * 72)
    lines.append(f"  Generated : {now}")
    lines.append(f"  Output dir: {out_dir}")

    # Statistics
    h1("STATISTICS")
    lines.append(f"  Articles successfully extracted : {len(articles)}")
    lines.append(f"  Pages skipped / failed          : {len(deduped_skipped)}")
    lines.append(f"  Total pages processed           : {len(articles) + len(deduped_skipped)}")
    lines.append(f"  Total words extracted           : {total_words:,}")
    lines.append(f"  Total estimated reading time    : {total_read} min")
    if articles:
        avg_words = total_words // len(articles)
        lines.append(f"  Average words per article       : {avg_words:,}")

    # Breakdown by extraction method
    if articles:
        methods = Counter(e.get("extraction_method_used", "unknown") for e in articles)
        h2("Extraction method breakdown")
        for method, count in methods.most_common():
            lines.append(f"    {method:<25} {count} article(s)")

    # Breakdown of skip reasons
    if deduped_skipped:
        reasons = Counter(s.get("reason", "unknown") for s in deduped_skipped)
        h2("Skip reason breakdown")
        for reason, count in reasons.most_common():
            lines.append(f"    {reason:<40} {count} page(s)")

    # Successfully scraped articles
    h1(f"SUCCESSFULLY SCRAPED ARTICLES  ({len(articles)})")
    if articles:
        col_w = 6
        lines.append(
            f"  {'#':>{col_w}}  {'Title':<50}  {'Author':<20}  "
            f"{'Published':<12}  {'Words':>6}  {'Read':>5}  {'Method':<14}  URL",
        )
        lines.append("  " + "-" * 160)
        for i, e in enumerate(articles, 1):
            title     = (e.get("title") or "-")[:50]
            author    = (e.get("author") or "-")[:20]
            published = (e.get("published_at") or "-")[:10]
            words     = e.get("word_count", 0)
            read      = e.get("reading_time_minutes", 0)
            method    = (e.get("extraction_method_used") or "-")[:14]
            url       = e.get("url", "-")
            lines.append(
                f"  {i:>{col_w}}  {title:<50}  {author:<20}  "
                f"{published:<12}  {words:>6}  {read:>4}m  {method:<14}  {url}",
            )
    else:
        lines.append("  (none)")

    # Links discovered per article
    h2("Links discovered per article")
    if articles:
        for e in articles:
            lcount = len(e.get("links", []) or [])
            title  = (e.get("title") or e.get("url", "-"))[:60]
            lines.append(f"    {lcount:>5} links  —  {title}")
    else:
        lines.append("    (none)")

    # Skipped / failed pages
    h1(f"SKIPPED / FAILED PAGES  ({len(deduped_skipped)})")
    if deduped_skipped:
        lines.append(f"  {'#':>5}  {'Reason':<40}  URL")
        lines.append("  " + "-" * 120)
        for i, s in enumerate(deduped_skipped, 1):
            reason = (s.get("reason") or "-")[:40]
            url    = s.get("url", "-")
            lines.append(f"  {i:>5}  {reason:<40}  {url}")
    else:
        lines.append("  (none)")

    # Output files
    h1("OUTPUT FILES")
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "summary.txt":
            rel = path.relative_to(out_dir)
            size_kb = path.stat().st_size / 1024
            lines.append(f"  {rel!s:<60}  {size_kb:>8.1f} KB")

    lines.append("")
    lines.append("=" * 72)
    lines.append("  END OF REPORT")
    lines.append("=" * 72)
    lines.append("")

    summary_path = out_dir / "summary.txt"
    try:
        summary_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write summary.txt: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
