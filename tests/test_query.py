"""Tests for llmparser.query - single-URL fetch and extraction API."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llmparser.items import ArticleSchema
from llmparser.query import FetchError, extract, fetch, fetch_html

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# extract() - pure HTML → ArticleSchema (no network)
# ---------------------------------------------------------------------------

class TestExtract:
    def test_returns_article_schema(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result, ArticleSchema)

    def test_title_extracted(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.title != ""

    def test_author_extracted(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.author == "Jane Smith"

    def test_published_at_extracted(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.published_at is not None
        assert "2024" in result.published_at

    def test_content_markdown_non_empty(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert len(result.content_markdown) > 50

    def test_content_text_non_empty(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.word_count > 0

    def test_word_count_positive(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.word_count > 10

    def test_reading_time_at_least_one(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.reading_time_minutes >= 1

    def test_content_blocks_list(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.content_blocks, list)

    def test_links_list(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.links, list)

    def test_images_list(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.images, list)

    def test_scraped_at_iso_format(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert "T" in result.scraped_at  # ISO 8601

    def test_extraction_method_recorded(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.extraction_method_used in ("readability", "trafilatura", "dom_heuristic")

    def test_article_score_int(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.article_score, int)

    def test_url_preserved(self):
        url = "https://example.com/blog/post"
        html = _read("article.html")
        result = extract(html, url=url)
        assert result.url == url

    def test_model_dump_returns_dict(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        d = result.model_dump()
        assert isinstance(d, dict)
        assert "title" in d
        assert "content_markdown" in d

    def test_empty_html_does_not_raise(self):
        result = extract("<html><body></body></html>", url="https://example.com/")
        assert isinstance(result, ArticleSchema)

    def test_no_url_does_not_raise(self):
        html = _read("article.html")
        result = extract(html)
        assert isinstance(result, ArticleSchema)

    def test_minimal_article(self):
        html = _read("minimal_article.html")
        result = extract(html, url="https://example.com/blog/minimal")
        assert result.word_count > 0

    def test_listing_page_has_low_score(self):
        html = _read("listing.html")
        result = extract(html, url="https://example.com/blog/")
        assert result.article_score < 35  # listing, not article

    def test_tags_are_list(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.tags, list)

    def test_raw_metadata_dict(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert isinstance(result.raw_metadata, dict)


# ---------------------------------------------------------------------------
# fetch_html() - HTTP fetch (mocked)
# ---------------------------------------------------------------------------

class TestFetchHtml:
    def _make_mock_response(self, body: str, charset: str = "utf-8") -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body.encode(charset)
        resp.headers.get_content_charset.return_value = charset
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_string(self):
        html = "<html><body><p>Hello world</p></body></html>"
        mock_resp = self._make_mock_response(html)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_html("https://example.com/blog/post")
        assert isinstance(result, str)
        assert "Hello world" in result

    def test_http_error_raises_fetch_error(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "https://example.com", 404, "Not Found", {}, None,
            ),
        ), pytest.raises(FetchError) as exc_info:
            fetch_html("https://example.com/blog/missing")
        assert exc_info.value.status == 404

    def test_url_error_raises_fetch_error(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ), pytest.raises(FetchError):
            fetch_html("https://example.com/blog/post")

    def test_invalid_scheme_raises_fetch_error(self):
        with pytest.raises(FetchError) as exc_info:
            fetch_html("ftp://example.com/file.txt")
        assert "scheme" in str(exc_info.value).lower()

    def test_fetch_error_carries_url(self):
        url = "https://example.com/blog/post"
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(url, 403, "Forbidden", {}, None),
        ), pytest.raises(FetchError) as exc_info:
            fetch_html(url)
        assert exc_info.value.url == url


# ---------------------------------------------------------------------------
# fetch() - combined fetch + extract (mocked network)
# ---------------------------------------------------------------------------

class TestFetch:
    def _patch_fetch_html(self, html: str):
        return patch("llmparser.query.fetch_html", return_value=html)

    def test_returns_article_schema(self):
        html = _read("article.html")
        with self._patch_fetch_html(html):
            result = fetch("https://example.com/blog/post")
        assert isinstance(result, ArticleSchema)

    def test_title_populated(self):
        html = _read("article.html")
        with self._patch_fetch_html(html):
            result = fetch("https://example.com/blog/post")
        assert result.title != ""

    def test_fetch_error_propagates(self):
        with patch(
            "llmparser.query.fetch_html",
            side_effect=FetchError("HTTP 404", url="https://x.com/", status=404),
        ), pytest.raises(FetchError) as exc_info:
            fetch("https://x.com/missing")
        assert exc_info.value.status == 404

    def test_word_count_in_result(self):
        html = _read("article.html")
        with self._patch_fetch_html(html):
            result = fetch("https://example.com/blog/post")
        assert result.word_count > 0

    def test_model_dump_serialisable(self):
        import json
        html = _read("article.html")
        with self._patch_fetch_html(html):
            result = fetch("https://example.com/blog/post")
        # Should not raise
        json.dumps(result.model_dump(), default=str)

    def test_render_js_false_uses_urllib(self):
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html) as mock_http, \
             patch("llmparser.query._fetch_html_playwright") as mock_pw:
            fetch("https://example.com/blog/post", render_js=False)
        mock_http.assert_called_once()
        mock_pw.assert_not_called()

    def test_render_js_true_uses_playwright(self):
        html = _read("article.html")
        with patch("llmparser.query._fetch_html_playwright", return_value=html) as mock_pw, \
             patch("llmparser.query.fetch_html") as mock_http:
            fetch("https://example.com/blog/post", render_js=True)
        mock_pw.assert_called_once()
        mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# Top-level import convenience
# ---------------------------------------------------------------------------

class TestTopLevelImport:
    def test_fetch_importable_from_package(self):
        from llmparser import fetch as top_fetch
        assert callable(top_fetch)

    def test_fetch_html_importable_from_package(self):
        from llmparser import fetch_html as top_fetch_html
        assert callable(top_fetch_html)

    def test_extract_importable_from_package(self):
        from llmparser import extract as top_extract
        assert callable(top_extract)

    def test_fetch_error_importable_from_package(self):
        from llmparser import FetchError as TopFetchError
        assert issubclass(TopFetchError, RuntimeError)
