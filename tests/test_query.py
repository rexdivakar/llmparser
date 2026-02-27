"""Tests for llmparser.query - single-URL fetch and extraction API."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llmparser.items import ArticleSchema
from llmparser.query import FetchError, extract, fetch, fetch_html, parse

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


# ---------------------------------------------------------------------------
# extract() — new block detection + quality fields
# ---------------------------------------------------------------------------

_CF_BLOCK_HTML = """<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
<h1>Just a moment...</h1>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
</body>
</html>"""

_CLEAN_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Deep Learning Guide | Tech Blog</title>
  <meta property="og:title" content="Deep Learning Guide">
</head>
<body>
<article>
  <h1>Deep Learning Guide</h1>
  <p>Deep learning is a subset of machine learning in which artificial neural
  networks—algorithms inspired by the human brain—learn from large amounts of
  data. Deep learning powers many AI applications including image recognition,
  speech synthesis, and natural language processing.</p>
  <p>This guide covers the key concepts, architectures, and practical
  applications of deep learning, from convolutional neural networks and
  recurrent architectures to transformers and large language models.</p>
</article>
</body>
</html>"""

_EMPTY_HTML = "<html><head></head><body><p>Loading...</p></body></html>"


class TestExtractBlockDetectionFields:
    """Verify that extract() populates the block/quality fields correctly."""

    def test_is_blocked_false_for_real_article(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        assert result.is_blocked is False
        assert result.block_type is None
        assert result.block_reason is None

    def test_is_blocked_true_for_cloudflare(self):
        result = extract(_CF_BLOCK_HTML, url="https://example.com")
        assert result.is_blocked is True
        assert result.block_type == "cloudflare"
        assert result.block_reason is not None

    def test_is_blocked_present_in_model_dump(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com")
        d = result.model_dump()
        assert "is_blocked" in d
        assert "block_type" in d
        assert "block_reason" in d

    def test_confidence_score_between_0_and_1(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        assert 0.0 <= result.confidence_score <= 1.0

    def test_confidence_score_matches_formula(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        expected = min(1.0, result.article_score / 80.0)
        assert abs(result.confidence_score - expected) < 1e-9

    def test_confidence_score_capped_at_1(self):
        # Force high article score by using a well-formed blog URL with rich content
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.confidence_score <= 1.0

    def test_confidence_score_in_model_dump(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        d = result.model_dump()
        assert "confidence_score" in d
        assert isinstance(d["confidence_score"], float)

    def test_is_empty_true_below_20_words(self):
        result = extract(_EMPTY_HTML, url="https://example.com")
        assert result.is_empty is True

    def test_is_empty_false_for_real_article(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        assert result.is_empty is False

    def test_is_empty_in_model_dump(self):
        result = extract(_CLEAN_ARTICLE_HTML, url="https://example.com/blog/dl")
        d = result.model_dump()
        assert "is_empty" in d
        assert isinstance(d["is_empty"], bool)

    def test_fetch_strategy_stored(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post",
                         fetch_strategy="static")
        assert result.fetch_strategy == "static"

    def test_page_type_stored(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post",
                         page_type="static_html")
        assert result.page_type == "static_html"

    def test_fetch_strategy_none_by_default(self):
        html = _read("article.html")
        result = extract(html, url="https://example.com/blog/post")
        assert result.fetch_strategy is None

    def test_captcha_block_detected(self):
        html = """<html><body>
        <div class="g-recaptcha" data-sitekey="abc123"></div>
        <script src="https://www.google.com/recaptcha/api.js"></script>
        </body></html>"""
        result = extract(html, url="https://example.com")
        assert result.is_blocked is True
        assert result.block_type == "captcha"


# ---------------------------------------------------------------------------
# fetch_html() — proxy parameter
# ---------------------------------------------------------------------------

class TestFetchHtmlProxy:
    def _make_mock_response(self, body: str = "<html><body>ok</body></html>") -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body.encode("utf-8")
        resp.headers.get_content_charset.return_value = "utf-8"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_no_proxy_uses_urlopen(self):
        mock_resp = self._make_mock_response()
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            fetch_html("https://example.com/blog/post")
        mock_urlopen.assert_called_once()

    def test_proxy_builds_opener(self):
        """When proxy is given, urllib.request.build_opener must be used."""
        mock_resp = self._make_mock_response()
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        with patch("urllib.request.build_opener", return_value=mock_opener) as mock_build, \
             patch("urllib.request.urlopen") as mock_urlopen:
            fetch_html("https://example.com/blog/post", proxy="http://p1:8080")
        mock_build.assert_called_once()
        mock_urlopen.assert_not_called()

    def test_proxy_injects_http_and_https_handlers(self):
        mock_resp = self._make_mock_response()
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        captured_handler = {}

        def capture_build(handler):
            captured_handler["h"] = handler
            return mock_opener

        with patch("urllib.request.ProxyHandler") as mock_ph, \
             patch("urllib.request.build_opener", side_effect=capture_build):
            fetch_html("https://example.com/blog/post", proxy="http://myproxy:3128")

        mock_ph.assert_called_once_with(
            {"http": "http://myproxy:3128", "https": "http://myproxy:3128"},
        )

    def test_proxy_none_does_not_call_build_opener(self):
        mock_resp = self._make_mock_response()
        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("urllib.request.build_opener") as mock_build:
            fetch_html("https://example.com/blog/post", proxy=None)
        mock_build.assert_not_called()

    def test_proxy_returns_correct_html(self):
        html = "<html><body><p>Proxied content</p></body></html>"
        mock_resp = self._make_mock_response(html)
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_resp
        with patch("urllib.request.build_opener", return_value=mock_opener), \
             patch("urllib.request.ProxyHandler"):
            result = fetch_html("https://example.com", proxy="http://p1:8080")
        assert "Proxied content" in result


# ---------------------------------------------------------------------------
# parse() — standalone pre-fetched HTML parser
# ---------------------------------------------------------------------------

class TestParseFunction:
    def test_returns_article_schema(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert isinstance(result, ArticleSchema)

    def test_fetch_strategy_is_pre_fetched(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert result.fetch_strategy == "pre_fetched"

    def test_page_type_is_none(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert result.page_type is None

    def test_url_preserved(self):
        html = _read("article.html")
        url = "https://example.com/blog/post"
        result = parse(html, url=url)
        assert result.url == url

    def test_default_url_is_empty(self):
        html = _read("article.html")
        result = parse(html)
        assert result.url == ""

    def test_no_network_calls(self):
        """parse() must never make HTTP requests."""
        html = _read("article.html")
        with patch("llmparser.query.fetch_html") as mock_fh, \
             patch("llmparser.query._fetch_html_playwright") as mock_pw:
            parse(html, url="https://example.com/blog/post")
        mock_fh.assert_not_called()
        mock_pw.assert_not_called()

    def test_block_detection_runs(self):
        html = _CF_BLOCK_HTML
        result = parse(html, url="https://example.com")
        assert result.is_blocked is True
        assert result.block_type == "cloudflare"

    def test_clean_html_not_blocked(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert result.is_blocked is False

    def test_title_extracted(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert result.title != ""

    def test_word_count_positive(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        assert result.word_count > 0

    def test_model_dump_contains_all_fields(self):
        html = _read("article.html")
        result = parse(html, url="https://example.com/blog/post")
        d = result.model_dump()
        for field in ("is_blocked", "block_type", "block_reason",
                      "confidence_score", "is_empty", "fetch_strategy"):
            assert field in d, f"Missing field: {field}"

    def test_importable_from_package(self):
        from llmparser import parse as top_parse
        assert callable(top_parse)


# ---------------------------------------------------------------------------
# fetch() — proxy_list + retry_on_block logic
# ---------------------------------------------------------------------------

class TestFetchProxyRetry:
    """Tests for proxy rotation and block-aware retry in fetch()."""

    def _make_article(self, *, is_blocked: bool = False,
                      block_type: str | None = None) -> ArticleSchema:
        return ArticleSchema(
            url="https://example.com/blog/post",
            is_blocked=is_blocked,
            block_type=block_type,
            block_reason="blocked" if is_blocked else None,
        )

    def _patch_do_fetch(self, articles: list[ArticleSchema]):
        """Patch adaptive_fetch_html to return successive mock articles."""
        # Each call to adaptive_fetch_html returns the same html;
        # we intercept extract() instead for simpler control.
        html = _read("article.html")
        fetch_html_patch = patch("llmparser.query.fetch_html", return_value=html)
        extract_patch = patch("llmparser.query.extract", side_effect=articles)
        return fetch_html_patch, extract_patch

    # --- No proxy ---

    def test_no_proxy_list_no_rotator(self):
        """Without proxy_list, fetch works normally."""
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html):
            result = fetch("https://example.com/blog/post")
        assert isinstance(result, ArticleSchema)

    def test_empty_proxy_list_treated_as_no_proxy(self):
        """proxy_list=[] must NOT crash — rotator max_retries=0."""
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html):
            result = fetch("https://example.com/blog/post", proxy_list=[])
        assert isinstance(result, ArticleSchema)

    # --- Proxy without block ---

    def test_single_proxy_used_when_no_block(self):
        """With one proxy and no block, result returned immediately."""
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html) as mock_fh:
            result = fetch("https://example.com/blog/post",
                           proxy_list=["http://p1:8080"])
        # fetch_html called once (static step in adaptive chain)
        assert mock_fh.call_count >= 1
        assert isinstance(result, ArticleSchema)

    # --- retry_on_block=False ---

    def test_retry_on_block_false_returns_blocked_article_immediately(self):
        """With retry_on_block=False, a blocked article is returned without retry."""
        blocked = self._make_article(is_blocked=True, block_type="cloudflare")
        unblocked = self._make_article(is_blocked=False)
        articles = [blocked, unblocked]
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html), \
             patch("llmparser.query.extract", side_effect=articles):
            result = fetch(
                "https://example.com/blog/post",
                proxy_list=["http://p1:8080", "http://p2:8080"],
                retry_on_block=False,
            )
        # extract only called once — no retry
        assert result.is_blocked is True

    # --- Block → rotate → success ---

    def test_block_triggers_proxy_rotation(self):
        """First call blocked → should rotate to next proxy and retry."""
        blocked = self._make_article(is_blocked=True, block_type="cloudflare")
        unblocked = self._make_article(is_blocked=False)
        html = _read("article.html")
        extract_calls = [blocked, unblocked]
        with patch("llmparser.query.fetch_html", return_value=html), \
             patch("llmparser.query.extract", side_effect=extract_calls) as mock_extract:
            result = fetch(
                "https://example.com/blog/post",
                proxy_list=["http://p1:8080", "http://p2:8080"],
                retry_on_block=True,
            )
        # extract called at least twice (initial + retry)
        assert mock_extract.call_count >= 2
        assert result.is_blocked is False

    def test_block_resolved_on_second_proxy(self):
        """Verify that the final returned article is unblocked after retry."""
        blocked = self._make_article(is_blocked=True, block_type="datadome")
        unblocked = self._make_article(is_blocked=False)
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html), \
             patch("llmparser.query.extract", side_effect=[blocked, unblocked]):
            result = fetch(
                "https://example.com/blog/post",
                proxy_list=["http://p1:8080", "http://p2:8080"],
                retry_on_block=True,
            )
        assert result.is_blocked is False
        assert result.block_type is None

    # --- All proxies blocked ---

    def test_returns_blocked_when_all_proxies_fail(self):
        """When every proxy still returns a block, return the last blocked article."""
        blocked1 = self._make_article(is_blocked=True, block_type="cloudflare")
        blocked2 = self._make_article(is_blocked=True, block_type="cloudflare")
        blocked3 = self._make_article(is_blocked=True, block_type="cloudflare")
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html), \
             patch("llmparser.query.extract",
                   side_effect=[blocked1, blocked2, blocked3]):
            result = fetch(
                "https://example.com/blog/post",
                proxy_list=["http://p1:8080", "http://p2:8080"],
                retry_on_block=True,
            )
        # Still returns something (doesn't raise)
        assert result is not None
        assert isinstance(result, ArticleSchema)

    # --- max retries capped at 5 ---

    def test_max_retries_capped_at_5(self):
        """With a 10-proxy list, no more than 5 retries should be attempted."""
        blocked = self._make_article(is_blocked=True, block_type="perimeterx")
        html = _read("article.html")
        # Provide 10 proxy entries but always return blocked
        proxies = [f"http://p{i}:8080" for i in range(10)]
        with patch("llmparser.query.fetch_html", return_value=html), \
             patch("llmparser.query.extract", return_value=blocked) as mock_extract:
            fetch(
                "https://example.com/blog/post",
                proxy_list=proxies,
                retry_on_block=True,
            )
        # max_block_retries = min(5, 10) = 5; total calls = 1 initial + ≤5 retries
        assert mock_extract.call_count <= 6

    # --- classification metadata ---

    def test_classification_metadata_in_raw_metadata(self):
        """fetch() must embed _classification dict in raw_metadata."""
        html = _read("article.html")
        with patch("llmparser.query.fetch_html", return_value=html):
            result = fetch("https://example.com/blog/post")
        assert "_classification" in result.raw_metadata
        cls = result.raw_metadata["_classification"]
        for key in ("reason", "confidence", "frameworks",
                    "amp_url", "feed_url", "body_word_count"):
            assert key in cls, f"Missing classification key: {key}"

    def test_render_js_with_proxy(self):
        """render_js=True + proxy threads proxy to _fetch_html_playwright."""
        html = _read("article.html")
        with patch("llmparser.query._fetch_html_playwright",
                   return_value=html) as mock_pw:
            result = fetch(
                "https://example.com/blog/post",
                render_js=True,
                proxy_list=["http://p1:8080"],
            )
        mock_pw.assert_called_once()
        _, kwargs = mock_pw.call_args
        assert kwargs.get("proxy") == "http://p1:8080"
        assert isinstance(result, ArticleSchema)


# ---------------------------------------------------------------------------
# ArticleSchema new fields — default values and round-trip serialisation
# ---------------------------------------------------------------------------

class TestArticleSchemaNewFields:
    def test_is_blocked_default_false(self):
        article = ArticleSchema(url="https://example.com")
        assert article.is_blocked is False

    def test_block_type_default_none(self):
        article = ArticleSchema(url="https://example.com")
        assert article.block_type is None

    def test_block_reason_default_none(self):
        article = ArticleSchema(url="https://example.com")
        assert article.block_reason is None

    def test_confidence_score_default_zero(self):
        article = ArticleSchema(url="https://example.com")
        assert article.confidence_score == 0.0

    def test_is_empty_default_false(self):
        article = ArticleSchema(url="https://example.com")
        assert article.is_empty is False

    def test_block_fields_set_correctly(self):
        article = ArticleSchema(
            url="https://example.com",
            is_blocked=True,
            block_type="cloudflare",
            block_reason="Cloudflare challenge detected",
            confidence_score=0.1,
        )
        assert article.is_blocked is True
        assert article.block_type == "cloudflare"
        assert article.block_reason == "Cloudflare challenge detected"

    def test_confidence_score_stored(self):
        article = ArticleSchema(url="https://example.com", confidence_score=0.75)
        assert article.confidence_score == pytest.approx(0.75)

    def test_is_empty_set_true(self):
        article = ArticleSchema(url="https://example.com", is_empty=True)
        assert article.is_empty is True

    def test_all_new_fields_in_model_dump(self):
        article = ArticleSchema(url="https://example.com")
        d = article.model_dump()
        for field in ("is_blocked", "block_type", "block_reason",
                      "confidence_score", "is_empty"):
            assert field in d, f"Missing field in model_dump(): {field}"

    def test_model_dump_json_serialisable(self):
        import json
        article = ArticleSchema(
            url="https://example.com",
            is_blocked=True,
            block_type="captcha",
            block_reason="hCaptcha widget detected",
            confidence_score=0.2,
            is_empty=False,
        )
        # Should not raise
        json.dumps(article.model_dump())
