"""Quick evaluation script — fetch a single URL and print all extracted fields.

Run:
    python evaluate.py [URL]

Requires the package to be installed:
    pip install -e ".[dev]"

The adaptive engine automatically identifies the page type (static HTML, JS SPA,
cookie-walled, paywalled, …) and selects the best fetch strategy.
Playwright is used automatically when needed:
    playwright install chromium
"""

from __future__ import annotations

import sys

from llmparser import FetchError, fetch
from llmparser.extractors.adaptive import PageType
from llmparser.items import ArticleSchema

# ── URL: pass as CLI arg or change the default below ──────────────────────────
URL = sys.argv[1] if len(sys.argv) > 1 else \
    "https://claude.com/blog/how-ai-helps-break-cost-barrier-cobol-modernization"
# ──────────────────────────────────────────────────────────────────────────────

SEP  = "─" * 72
SEP2 = "━" * 72

_STRATEGY_LABELS: dict[str, str] = {
    "static":              "Static HTTP (urllib)",
    "amp":                 "AMP equivalent URL",
    "mobile_ua":           "Mobile User-Agent retry",
    "playwright":          "Playwright (headless Chromium)",
    "playwright_forced":   "Playwright (forced by caller)",
    "playwright_fallback": "Playwright (auto fallback)",
    "static_best_effort":  "Static HTTP — best effort (partial content)",
    "pre_fetched":         "Pre-fetched HTML (parse only)",
}

_TYPE_LABELS: dict[str, str] = {
    PageType.STATIC_HTML:   "Static HTML",
    PageType.JS_SPA:        "JavaScript SPA",
    PageType.COOKIE_WALLED: "Cookie / GDPR wall",
    PageType.PAYWALLED:     "Paywalled",
    PageType.UNKNOWN:       "Unknown",
}

_W = 18   # label column width for key : value rows


# ─────────────────────────────────────────────────────────────────────────────
# Section printers
# ─────────────────────────────────────────────────────────────────────────────

def _hdr(title: str) -> None:
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)


def _row(label: str, value: object) -> None:
    print(f"  {label:<{_W}}  {value}")


def _print_page_analysis(article: ArticleSchema, pre_html: str, pre_url: str) -> None:
    """Full classification signals — all fields from PageSignals + ClassificationResult."""
    try:
        from llmparser.extractors.adaptive import classify_page
        cr = classify_page(pre_html, pre_url)
        sig = cr.signals
    except Exception:
        return

    _hdr("PAGE ANALYSIS  — classify_page() signals")
    _row("page_type",         _TYPE_LABELS.get(str(cr.page_type), str(cr.page_type)))
    _row("confidence",        f"{cr.confidence:.0%}")
    _row("recommended",       _STRATEGY_LABELS.get(
        cr.recommended_strategy, cr.recommended_strategy))
    _row("strategy_used",     _STRATEGY_LABELS.get(
        article.fetch_strategy or "", article.fetch_strategy or "—"))
    _row("reason",            cr.reason)
    print(f"  {SEP}")
    _row("body_word_count",   f"{sig.body_word_count}  (raw HTML, before extraction)")
    _row("has_meta_title",    sig.has_meta_title)
    _row("has_article_schema",sig.has_article_schema)
    _row("is_js_spa",         sig.is_js_spa)
    _row("js_root_found",     sig.js_root_found)
    _row("is_cookie_walled",  sig.is_cookie_walled)
    _row("is_paywalled",      sig.is_paywalled)
    _row("amp_url",           sig.amp_url or "—")
    _row("feed_url",          sig.feed_url or "—")
    _row("frameworks",        ", ".join(sig.frameworks_detected)
         if sig.frameworks_detected else "—")


def _print_article_fields(article: ArticleSchema) -> None:
    """Core identity and provenance fields."""
    _hdr("ARTICLE FIELDS")
    _row("url",               article.url)
    _row("canonical_url",     article.canonical_url or "—")
    _row("title",             article.title or "(empty)")
    _row("author",            article.author or "—")
    _row("published_at",      article.published_at or "—")
    _row("updated_at",        article.updated_at or "—")
    _row("site_name",         article.site_name or "—")
    _row("language",          article.language or "—")
    _row("tags",              ", ".join(article.tags) if article.tags else "—")
    print(f"  {SEP}")
    _row("word_count",        article.word_count)
    _row("reading_time",      f"{article.reading_time_minutes} min")
    _row("article_score",     article.article_score)
    _row("confidence_score",  f"{article.confidence_score:.4f}  ({article.confidence_score:.1%})")
    _row("extraction_method", article.extraction_method_used)
    _row("fetch_strategy",    _STRATEGY_LABELS.get(
        article.fetch_strategy or "", article.fetch_strategy or "—"))
    _row("page_type",         _TYPE_LABELS.get(article.page_type or "", article.page_type or "—"))
    _row("scraped_at",        article.scraped_at)


def _print_block_detection(article: ArticleSchema) -> None:
    """Anti-bot / block detection results populated by detect_block()."""
    _hdr("BLOCK DETECTION  — detect_block() result")
    blocked_lbl = f"\033[31mYES  [{article.block_type}]\033[0m" if article.is_blocked \
        else "\033[32mNO\033[0m"
    _row("is_blocked",    blocked_lbl)
    _row("block_type",    article.block_type or "—")
    _row("block_reason",  article.block_reason or "—")
    _row("is_empty",      article.is_empty)
    _row("confidence",    f"{article.confidence_score:.4f}  (article_score / 80.0, capped at 1.0)")


def _print_quality_signals(article: ArticleSchema) -> None:
    """Quality scoring breakdown."""
    _hdr("QUALITY SIGNALS")
    score = article.article_score
    conf  = article.confidence_score
    wc    = article.word_count

    quality = "HIGH" if conf >= 0.75 else ("MEDIUM" if conf >= 0.40 else "LOW")
    density = "dense" if wc > 500 else ("normal" if wc > 100 else "sparse")

    _row("article_score",    f"{score}  (raw heuristic score)")
    _row("confidence_score", f"{conf:.4f}  → {quality} quality")
    _row("word_count",       f"{wc}  ({density})")
    _row("reading_time",     f"{article.reading_time_minutes} min")
    _row("is_empty",         f"{article.is_empty}  (True when word_count < 20)")
    _row("extraction",       article.extraction_method_used)
    _row("images_found",     len(article.images))
    _row("links_found",      len(article.links))
    _row("blocks_found",     len(article.content_blocks))


def _print_raw_metadata(article: ArticleSchema) -> None:
    """Full raw_metadata dump — OG, Twitter, JSON-LD, and classification cache."""
    _hdr("RAW METADATA  — article.raw_metadata")
    raw = article.raw_metadata

    for section, label in (
        ("og", "OPEN GRAPH"), ("twitter", "TWITTER CARD"), ("jsonld", "JSON-LD"),
    ):
        data: dict = raw.get(section) or {}  # type: ignore[assignment]
        print(f"\n  {label}:")
        if data:
            for k, v in sorted(data.items()):
                v_str = str(v)
                if len(v_str) > 100:
                    v_str = v_str[:97] + "…"
                print(f"    {k:<40}  {v_str}")
        else:
            print("    (none)")

    cls: dict = raw.get("_classification") or {}  # type: ignore[assignment]
    if cls:
        print("\n  _CLASSIFICATION  (cached from adaptive engine):")
        for k, v in sorted(cls.items()):
            v_str = str(v)
            if len(v_str) > 100:
                v_str = v_str[:97] + "…"
            print(f"    {k:<40}  {v_str}")


def _print_content_blocks(article: ArticleSchema) -> None:
    """Structured content blocks extracted from the DOM."""
    _hdr(f"CONTENT BLOCKS  ({len(article.content_blocks)} blocks)")
    if not article.content_blocks:
        print("  (none extracted)")
        return

    type_counts: dict[str, int] = {}
    for blk in article.content_blocks:
        t = blk.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("  Type breakdown: " + "  ".join(f"{t}={n}" for t, n in sorted(type_counts.items())))
    print(f"  {SEP}")

    for i, blk in enumerate(article.content_blocks):
        btype = blk.get("type", "?")
        text  = (
            blk.get("text") or blk.get("markdown") or
            blk.get("content") or blk.get("html") or ""
        )
        level = blk.get("level", "")
        level_str = f" [h{level}]" if level else ""
        preview = str(text).replace("\n", " ").strip()
        if len(preview) > 100:
            preview = preview[:97] + "…"
        print(f"  [{i:>3}] {btype:<12}{level_str:<6}  {preview}")


def _print_images(article: ArticleSchema) -> None:
    if not article.images:
        return
    _hdr(f"IMAGES  ({len(article.images)})")
    for img in article.images:
        alt     = img.get("alt") or ""
        caption = img.get("caption") or ""
        line    = f"  {img['url']}"
        if alt:
            line += f"\n    alt     : {alt}"
        if caption:
            line += f"\n    caption : {caption}"
        print(line)


def _print_links(article: ArticleSchema) -> None:
    if not article.links:
        return
    internal = [lk for lk in article.links if lk.get("is_internal")]
    external = [lk for lk in article.links if not lk.get("is_internal")]
    _hdr(f"LINKS  ({len(internal)} internal / {len(external)} external)")
    for lk in (internal + external):
        kind = "int" if lk.get("is_internal") else "ext"
        text = (lk.get("text") or "").strip()[:60]
        rel  = lk.get("rel") or ""
        line = f"  [{kind}] {lk['href']}"
        if text:
            line += f'\n         text : "{text}"'
        if rel:
            line += f"\n         rel  : {rel}"
        print(line)


def _print_rag_preview(article: ArticleSchema) -> None:
    """Show how the article splits into RAG-ready chunks."""
    try:
        chunks = article.to_chunks()
    except Exception:
        return
    if not chunks:
        return
    _hdr(f"RAG CHUNKS  ({len(chunks)} chunks via to_chunks())")
    for i, chunk in enumerate(chunks):
        cid   = getattr(chunk, "chunk_id", i)
        ctype = getattr(chunk, "chunk_type", "?")
        text  = getattr(chunk, "text", "") or ""
        wc    = len(text.split())
        preview = text.replace("\n", " ").strip()
        if len(preview) > 110:
            preview = preview[:107] + "…"
        print(f"  [{i:>2}] id={cid!s:<30}  type={ctype:<12}  words={wc:<5}  {preview}")


def _print_content_markdown(article: ArticleSchema) -> None:
    _hdr(f"CONTENT MARKDOWN  ({article.word_count} words)")
    md = (article.content_markdown or "").strip()
    if md:
        print(md)
    else:
        print("  (empty)")
    print(SEP2)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

print(SEP2)
print(f"  Fetching: {URL}")
print(SEP2)

try:
    article = fetch(URL)
except FetchError as exc:
    print(f"[ERROR] Could not fetch {exc.url!r} (HTTP {exc.status}): {exc}")
    raise SystemExit(1) from exc
except Exception as exc:
    print(f"[ERROR] Unexpected error fetching {URL!r}: {exc}")
    raise SystemExit(1) from exc

# PAGE ANALYSIS — re-fetch static HTML for classification display
# (the adaptive engine already did the real classification during fetch())
try:
    from llmparser.query import fetch_html as _fetch_html
    _pre_html = _fetch_html(URL)
    _print_page_analysis(article, _pre_html, URL)
except Exception as _exc:
    import logging as _log
    _log.getLogger(__name__).debug("Page analysis display failed: %s", _exc)

_print_article_fields(article)
_print_block_detection(article)
_print_quality_signals(article)
_print_raw_metadata(article)
_print_content_blocks(article)
_print_images(article)
_print_links(article)
_print_rag_preview(article)
_print_content_markdown(article)
