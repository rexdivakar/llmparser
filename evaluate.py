"""Quick evaluation script — fetch a single URL and print all extracted fields.

Run:
    python evaluate.py

Requires the package to be installed:
    pip install -e ".[dev]"

The adaptive engine automatically identifies the page type (static HTML, JS SPA,
cookie-walled, paywalled, …) and selects the best fetch strategy.
Playwright is used automatically when needed:
    playwright install chromium
"""

from blog_scraper import FetchError, fetch
from blog_scraper.extractors.adaptive import PageType
from blog_scraper.items import ArticleSchema

# ── change this URL to evaluate any page ──────────────────────────────────────
URL = "https://9to5linux.com/clonezilla-live-3-3-1-released-with-linux-6-18-lts-improved-bitlocker-support"
# ──────────────────────────────────────────────────────────────────────────────

SEP = "-" * 72

_STRATEGY_LABELS = {
    "static":              "Static HTTP (urllib)",
    "amp":                 "AMP equivalent URL",
    "mobile_ua":           "Mobile User-Agent retry",
    "playwright":          "Playwright (headless Chromium)",
    "playwright_forced":   "Playwright (forced by caller)",
    "playwright_fallback": "Playwright (auto fallback)",
    "static_best_effort":  "Static HTTP — best effort (partial content)",
}

_TYPE_LABELS = {
    PageType.STATIC_HTML.value:   "Static HTML",
    PageType.JS_SPA.value:        "JavaScript SPA",
    PageType.COOKIE_WALLED.value: "Cookie / GDPR wall",
    PageType.PAYWALLED.value:     "Paywalled",
    PageType.UNKNOWN.value:       "Unknown",
}


def _print_analysis(article: ArticleSchema) -> None:
    """Print the page-type analysis section from embedded classification signals.

    Classification data is embedded in raw_metadata['_classification'] by
    fetch() — no second HTTP request needed.
    """
    clf = article.raw_metadata.get("_classification") if article.raw_metadata else None

    print("\nPAGE ANALYSIS")
    type_label = _TYPE_LABELS.get(article.page_type or "", article.page_type or "—")
    confidence_str = f" (confidence: {clf['confidence']:.0%})" if clf else ""
    print(f"  Type       : {type_label}{confidence_str}")
    print(f"  Strategy   : {_STRATEGY_LABELS.get(article.fetch_strategy or '', article.fetch_strategy or '—')}")

    if clf:
        fw = ", ".join(clf["frameworks"]) if clf.get("frameworks") else "—"
        print(f"  Reason     : {clf['reason']}")
        print(f"  Frameworks : {fw}")
        print(f"  AMP URL    : {clf.get('amp_url') or '—'}")
        print(f"  Feed URL   : {clf.get('feed_url') or '—'}")
        print(f"  Body words : {clf.get('body_word_count', '—')} (raw, pre-extraction)")
    else:
        print("  (No classification signals — render_js=True was used)")


def _print_article(article: ArticleSchema) -> None:
    print(SEP)
    print(f"  URL          : {article.url}")
    print(f"  Canonical    : {article.canonical_url}")
    print(f"  Title        : {article.title}")
    print(f"  Author       : {article.author or '—'}")
    print(f"  Published at : {article.published_at or '—'}")
    print(f"  Updated at   : {article.updated_at or '—'}")
    print(f"  Site name    : {article.site_name or '—'}")
    print(f"  Language     : {article.language or '—'}")
    print(f"  Tags         : {', '.join(article.tags) if article.tags else '—'}")
    print(f"  Word count   : {article.word_count}")
    print(f"  Reading time : {article.reading_time_minutes} min")
    print(f"  Extraction   : {article.extraction_method_used}")
    print(f"  Fetch strat  : {_STRATEGY_LABELS.get(article.fetch_strategy or '', article.fetch_strategy or '—')}")
    print(f"  Page type    : {_TYPE_LABELS.get(article.page_type or '', article.page_type or '—')}")
    print(f"  Article score: {article.article_score}")
    print(f"  Scraped at   : {article.scraped_at}")
    print(SEP)

    if article.summary:
        print("\nSUMMARY")
        print(article.summary)

    if article.images:
        print(f"\nIMAGES ({len(article.images)})")
        for img in article.images[:10]:
            alt = img.get("alt") or ""
            print(f"  {img['url']}" + (f"  [{alt}]" if alt else ""))
        if len(article.images) > 10:
            print(f"  … and {len(article.images) - 10} more")

    if article.links:
        internal = [lnk for lnk in article.links if lnk.get("is_internal")]
        external = [lnk for lnk in article.links if not lnk.get("is_internal")]
        print(f"\nLINKS  ({len(internal)} internal / {len(external)} external)")
        for lnk in (internal + external)[:15]:
            kind = "int" if lnk.get("is_internal") else "ext"
            text = (lnk.get("text") or "").strip()[:50]
            suffix = f'  "{text}"' if text else ""
            print(f"  [{kind}] {lnk['href']}{suffix}")
        total = len(article.links)
        if total > 15:
            print(f"  … and {total - 15} more")

    print(f"\nCONTENT MARKDOWN  ({article.word_count} words)\n{SEP}")
    print(article.content_markdown or "(empty)")
    print(SEP)


# ── fetch (adaptive engine handles everything automatically) ───────────────────

print(f"Fetching: {URL}")

try:
    article = fetch(URL)
except FetchError as exc:
    print(f"[ERROR] Could not fetch {exc.url!r} (HTTP {exc.status}): {exc}")
    raise SystemExit(1) from exc
except Exception as exc:
    print(f"[ERROR] Unexpected error fetching {URL!r}: {exc}")
    raise SystemExit(1) from exc

_print_analysis(article)
_print_article(article)

# # As a plain dict (JSON-serialisable)
# import json, sys
# json.dump(article.model_dump(), sys.stdout, indent=2, ensure_ascii=False)
