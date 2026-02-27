"""Tests for llmparser.parser — LLMParser high-level class."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from llmparser.items import ArticleSchema
from llmparser.parser import LLMParser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(**kwargs) -> ArticleSchema:
    defaults = {"url": "https://example.com/blog/post", "is_blocked": False}
    defaults.update(kwargs)
    return ArticleSchema(**defaults)



# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestLLMParserInit:
    def test_defaults(self):
        parser = LLMParser()
        assert parser._proxy_list is None
        assert parser._proxy_rotation == "round_robin"
        assert parser._retry_on_block is True
        assert parser._timeout == 30
        assert parser._render_js is False

    def test_with_proxy_list(self):
        proxies = ["http://p1:8080", "http://p2:8080"]
        parser = LLMParser(proxy_list=proxies)
        assert parser._proxy_list == proxies

    def test_proxy_rotation_random(self):
        parser = LLMParser(proxy_rotation="random")
        assert parser._proxy_rotation == "random"

    def test_retry_on_block_false(self):
        parser = LLMParser(retry_on_block=False)
        assert parser._retry_on_block is False

    def test_custom_timeout(self):
        parser = LLMParser(timeout=60)
        assert parser._timeout == 60

    def test_render_js_true(self):
        parser = LLMParser(render_js=True)
        assert parser._render_js is True

    def test_full_config(self):
        parser = LLMParser(
            proxy_list=["http://p1:8080"],
            proxy_rotation="random",
            retry_on_block=False,
            timeout=45,
            render_js=True,
        )
        assert parser._proxy_list == ["http://p1:8080"]
        assert parser._proxy_rotation == "random"
        assert parser._retry_on_block is False
        assert parser._timeout == 45
        assert parser._render_js is True


# ---------------------------------------------------------------------------
# fetch() method
# ---------------------------------------------------------------------------

class TestLLMParserFetch:
    def test_returns_article_schema(self):
        parser = LLMParser()
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            result = parser.fetch("https://example.com/blog/post")
        assert result is article
        mock_fetch.assert_called_once()

    def test_passes_url_positionally(self):
        parser = LLMParser()
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        args, _ = mock_fetch.call_args
        assert args[0] == "https://example.com/blog/post"

    def test_uses_constructor_proxy_list(self):
        proxies = ["http://p1:8080", "http://p2:8080"]
        parser = LLMParser(proxy_list=proxies)
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        _, kwargs = mock_fetch.call_args
        assert kwargs["proxy_list"] == proxies

    def test_uses_constructor_retry_on_block(self):
        parser = LLMParser(retry_on_block=False)
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        _, kwargs = mock_fetch.call_args
        assert kwargs["retry_on_block"] is False

    def test_uses_constructor_timeout(self):
        parser = LLMParser(timeout=45)
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        _, kwargs = mock_fetch.call_args
        assert kwargs["timeout"] == 45

    def test_uses_constructor_render_js(self):
        parser = LLMParser(render_js=True)
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        _, kwargs = mock_fetch.call_args
        assert kwargs["render_js"] is True

    def test_kwarg_overrides_constructor_timeout(self):
        parser = LLMParser(timeout=30)
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post", timeout=90)
        _, kwargs = mock_fetch.call_args
        assert kwargs["timeout"] == 90

    def test_kwarg_overrides_constructor_proxy_list(self):
        parser = LLMParser(proxy_list=["http://p1:8080"])
        article = _make_article()
        override = ["http://p2:8080"]
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post", proxy_list=override)
        _, kwargs = mock_fetch.call_args
        assert kwargs["proxy_list"] == override

    def test_extra_kwargs_forwarded(self):
        parser = LLMParser()
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post", user_agent="TestBot/1.0")
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("user_agent") == "TestBot/1.0"

    def test_no_proxy_by_default(self):
        parser = LLMParser()
        article = _make_article()
        with patch("llmparser.parser._fetch", return_value=article) as mock_fetch:
            parser.fetch("https://example.com/blog/post")
        _, kwargs = mock_fetch.call_args
        # proxy_list default is None
        assert kwargs.get("proxy_list") is None


# ---------------------------------------------------------------------------
# parse() method
# ---------------------------------------------------------------------------

class TestLLMParserParse:
    def test_returns_article_schema(self):
        parser = LLMParser()
        html = "<html><body><p>Content here</p></body></html>"
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            result = parser.parse(html)
        assert result is article
        mock_parse.assert_called_once()

    def test_passes_html(self):
        parser = LLMParser()
        html = "<html><body>Hello</body></html>"
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            parser.parse(html)
        args, _ = mock_parse.call_args
        assert args[0] == html

    def test_passes_url(self):
        parser = LLMParser()
        html = "<html><body>Content</body></html>"
        url = "https://example.com/blog/post"
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            parser.parse(html, url=url)
        _, kwargs = mock_parse.call_args
        assert kwargs["url"] == url

    def test_default_url_is_empty_string(self):
        parser = LLMParser()
        html = "<html><body>Content</body></html>"
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            parser.parse(html)
        _, kwargs = mock_parse.call_args
        assert kwargs["url"] == ""

    def test_no_network_calls(self):
        """parse() must not make any network requests."""
        parser = LLMParser()
        html = "<html><body><p>Direct content</p></body></html>"
        with patch("llmparser.parser._fetch") as mock_fetch, \
             patch("llmparser.parser._parse", return_value=_make_article()):
            parser.parse(html)
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# parse_from_browser method
# ---------------------------------------------------------------------------

class TestLLMParserParseFromBrowser:
    def _make_mock_page(self, html: str, url: str) -> MagicMock:
        page = MagicMock()
        page.content.return_value = html
        page.url = url
        return page

    def test_calls_page_content(self):
        parser = LLMParser()
        html = "<html><body>Article text here</body></html>"
        page = self._make_mock_page(html, "https://example.com")
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article):
            parser.parse_from_browser(page)
        page.content.assert_called_once()

    def test_passes_page_html_to_parse(self):
        parser = LLMParser()
        html = "<html><body>Browser content</body></html>"
        page = self._make_mock_page(html, "https://example.com/page")
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            parser.parse_from_browser(page)
        args, _ = mock_parse.call_args
        assert args[0] == html

    def test_passes_page_url(self):
        parser = LLMParser()
        url = "https://example.com/live/article"
        page = self._make_mock_page("<html><body>Content</body></html>", url)
        article = _make_article(url=url)
        with patch("llmparser.parser._parse", return_value=article) as mock_parse:
            parser.parse_from_browser(page)
        _, kwargs = mock_parse.call_args
        assert kwargs["url"] == url

    def test_returns_article_schema(self):
        parser = LLMParser()
        page = self._make_mock_page("<html><body>Text</body></html>", "https://example.com")
        article = _make_article()
        with patch("llmparser.parser._parse", return_value=article):
            result = parser.parse_from_browser(page)
        assert result is article

    def test_no_fetch_calls_made(self):
        """parse_from_browser must not trigger any HTTP fetches."""
        parser = LLMParser()
        page = self._make_mock_page("<html><body>Text</body></html>", "https://example.com")
        with patch("llmparser.parser._fetch") as mock_fetch, \
             patch("llmparser.parser._parse", return_value=_make_article()):
            parser.parse_from_browser(page)
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Public API imports
# ---------------------------------------------------------------------------

class TestLLMParserImports:
    def test_importable_from_top_level(self):
        from llmparser import LLMParser as TopLLMParser
        assert TopLLMParser is LLMParser

    def test_blockresult_importable(self):
        from llmparser import BlockResult
        from llmparser.extractors.block_detection import BlockResult as DirectBlockResult
        assert BlockResult is DirectBlockResult

    def test_proxy_config_importable(self):
        from llmparser import ProxyConfig
        from llmparser.proxy import ProxyConfig as DirectProxyConfig
        assert ProxyConfig is DirectProxyConfig

    def test_parse_importable_from_top_level(self):
        from llmparser import parse
        assert callable(parse)
