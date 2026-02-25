"""Unit tests for the adaptive page classifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from blog_scraper.extractors.adaptive import (
    ClassificationResult,
    PageSignals,
    PageType,
    _detect_signals,
    classify_page,
)
from blog_scraper.extractors.main_content import _preprocess_html

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _detect_signals
# ---------------------------------------------------------------------------

class TestDetectSignals:
    def test_fingerprinted_spa(self, jsapp_html: str) -> None:
        """Standard CRA/Next.js pattern — detected via framework fingerprints."""
        sig = _detect_signals(jsapp_html)
        assert sig.is_js_spa
        assert sig.body_word_count < 20

    def test_fingerprint_less_spa(self) -> None:
        """Ultra-thin body + external scripts → SPA even without known framework."""
        html = _read("spa_no_framework.html")
        sig = _detect_signals(html, "https://services.vfsglobal.com/usa/en/ind/apply-passport")
        assert sig.is_js_spa
        assert sig.body_word_count < 10

    def test_good_static_article(self, article_html: str) -> None:
        sig = _detect_signals(article_html, "https://example.com/blog/post")
        assert sig.body_word_count >= 100
        assert not sig.is_js_spa

    def test_cookie_wall_detected(self) -> None:
        html = _read("cookie_wall.html")
        sig = _detect_signals(html)
        assert sig.is_cookie_walled

    def test_template_excluded_from_word_count(self) -> None:
        """<template> cookie content must NOT inflate body_word_count."""
        html = _read("wpconsent_template.html")
        sig = _detect_signals(html)
        # The template holds cookie text — must not trigger cookie wall
        assert not sig.is_cookie_walled
        # Must count real article words (≥ 60), not template noise
        assert sig.body_word_count >= 60

    def test_template_not_flagged_as_cookie_wall(self) -> None:
        """<template> cookie text must not misclassify the page as COOKIE_WALLED."""
        html = _read("wpconsent_template.html")
        result = classify_page(html)
        assert result.page_type != PageType.COOKIE_WALLED

    def test_amp_url_detected(self) -> None:
        html = """<html><head>
            <link rel="amphtml" href="https://example.com/amp/post"/>
            <title>Article</title>
        </head><body></body></html>"""
        sig = _detect_signals(html)
        assert sig.amp_url == "https://example.com/amp/post"

    def test_feed_url_detected(self) -> None:
        html = """<html><head>
            <link rel="alternate" type="application/rss+xml" href="/feed.xml"/>
            <title>Blog</title>
        </head><body></body></html>"""
        sig = _detect_signals(html)
        assert sig.feed_url == "/feed.xml"

    def test_body_words_excludes_noise_tags(self) -> None:
        """nav/header/footer/script content must not count toward body_word_count."""
        html = """<html><body>
            <nav>""" + ("nav word " * 200) + """</nav>
            <article><p>Real article content here with just a few words.</p></article>
        </body></html>"""
        sig = _detect_signals(html)
        # Should be much less than 200 (nav stripped)
        assert sig.body_word_count < 50

    def test_no_scripts_not_spa(self) -> None:
        """Page with 1-word body but no external scripts must NOT be classified as SPA."""
        html = "<html><head><title>Hi</title></head><body><p>Hi</p></body></html>"
        sig = _detect_signals(html)
        assert not sig.is_js_spa

    def test_next_js_framework_detected(self) -> None:
        html = """<html><head>
            <script src="/_next/static/chunks/main.js"></script>
        </head><body><div id="__next"></div></body></html>"""
        sig = _detect_signals(html)
        assert "Next.js" in sig.frameworks_detected


# ---------------------------------------------------------------------------
# classify_page
# ---------------------------------------------------------------------------

class TestClassifyPage:
    def test_spa_recommends_playwright(self, jsapp_html: str) -> None:
        result = classify_page(jsapp_html)
        assert result.page_type == PageType.JS_SPA
        assert result.recommended_strategy == "playwright"
        assert result.confidence >= 0.80

    def test_spa_no_framework_recommends_playwright(self) -> None:
        """VFS Global pattern: ultra-thin + scripts → playwright."""
        html = _read("spa_no_framework.html")
        result = classify_page(html, "https://services.vfsglobal.com/usa/en/ind")
        assert result.page_type == PageType.JS_SPA
        assert result.recommended_strategy == "playwright"

    def test_spa_with_amp_recommends_amp(self) -> None:
        html = """<html><head>
            <link rel="amphtml" href="https://example.com/amp/post"/>
            <script src="/_next/static/chunks/main.js"></script>
            <title>Post</title>
        </head><body><div id="__next"></div></body></html>"""
        result = classify_page(html)
        assert result.page_type == PageType.JS_SPA
        assert result.recommended_strategy == "amp"

    def test_static_article_recommends_static(self, article_html: str) -> None:
        result = classify_page(article_html, "https://example.com/blog/post")
        assert result.page_type == PageType.STATIC_HTML
        assert result.recommended_strategy == "static"
        assert result.confidence >= 0.85

    def test_cookie_wall_recommends_playwright(self) -> None:
        html = _read("cookie_wall.html")
        result = classify_page(html)
        assert result.page_type == PageType.COOKIE_WALLED
        assert result.recommended_strategy == "playwright"

    def test_result_has_all_fields(self, article_html: str) -> None:
        result = classify_page(article_html)
        assert isinstance(result, ClassificationResult)
        assert isinstance(result.signals, PageSignals)
        assert result.page_type in list(PageType)
        assert 0.0 <= result.confidence <= 1.0
        assert result.reason
        assert result.recommended_strategy


# ---------------------------------------------------------------------------
# _preprocess_html — template stripping
# ---------------------------------------------------------------------------

class TestPreprocessHtml:
    def test_template_removed_before_readability(self) -> None:
        """lxml moves <template> children into body; regex must remove them first."""
        html = _read("wpconsent_template.html")
        clean = _preprocess_html(html)
        assert "<template" not in clean
        assert "wpconsent-modal" not in clean

    def test_article_content_preserved(self) -> None:
        html = _read("wpconsent_template.html")
        clean = _preprocess_html(html)
        assert "Clonezilla" in clean

    def test_cookie_consent_selectors_removed(self) -> None:
        html = "<html><body><p>Article text here.</p><div class='cookie-banner'><p>Accept cookies</p></div></body></html>"
        clean = _preprocess_html(html)
        assert "cookie-banner" not in clean
        assert "Article text" in clean

    def test_nested_template_removed(self) -> None:
        """Deeply nested template content must be stripped."""
        html = """<html><body>
            <p>Good content word.</p>
            <template id="outer"><div><template id="inner">Inner cookie text.</template></div></template>
        </body></html>"""
        clean = _preprocess_html(html)
        assert "Inner cookie text" not in clean
        assert "Good content" in clean

    def test_malformed_html_returns_something(self) -> None:
        """Never raises — always returns a string even on broken input."""
        result = _preprocess_html("<<<not html>>>")
        assert isinstance(result, str)
