"""Unit tests for URL normalization utilities."""

from __future__ import annotations

from blog_scraper.extractors.urlnorm import (
    extract_domain,
    is_non_content_url,
    normalize_url,
    url_to_slug,
)


class TestNormalizeUrl:
    def test_removes_utm_source(self):
        url = "https://example.com/blog/post?utm_source=twitter&id=1"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "id=1" in result

    def test_removes_utm_medium(self):
        url = "https://example.com/blog?utm_medium=email"
        result = normalize_url(url)
        assert "utm_medium" not in result

    def test_removes_fbclid(self):
        url = "https://example.com/post?fbclid=abc123"
        result = normalize_url(url)
        assert "fbclid" not in result

    def test_removes_gclid(self):
        url = "https://example.com/post?gclid=xyz"
        result = normalize_url(url)
        assert "gclid" not in result

    def test_strips_fragment(self):
        url = "https://example.com/post#section-2"
        result = normalize_url(url)
        assert "#" not in result
        assert "section-2" not in result

    def test_lowercases_scheme(self):
        url = "HTTPS://Example.COM/Post"
        result = normalize_url(url)
        assert result.startswith("https://example.com/")

    def test_removes_default_http_port(self):
        url = "http://example.com:80/post"
        result = normalize_url(url)
        assert ":80" not in result

    def test_removes_default_https_port(self):
        url = "https://example.com:443/post"
        result = normalize_url(url)
        assert ":443" not in result

    def test_keeps_non_default_port(self):
        url = "https://example.com:8443/post"
        result = normalize_url(url)
        assert ":8443" in result

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/search?q=python&page=2"
        result = normalize_url(url)
        assert "q=python" in result
        assert "page=2" in result

    def test_sorts_query_params(self):
        url1 = "https://example.com/post?b=2&a=1"
        url2 = "https://example.com/post?a=1&b=2"
        assert normalize_url(url1) == normalize_url(url2)

    def test_removes_all_utm_params(self):
        url = (
            "https://example.com/post"
            "?utm_source=newsletter"
            "&utm_medium=email"
            "&utm_campaign=spring"
            "&utm_term=python"
            "&utm_content=cta"
        )
        result = normalize_url(url)
        assert "utm_" not in result
        # Path should still be there
        assert "example.com/post" in result

    def test_empty_query_normalized(self):
        url = "https://example.com/post?"
        result = normalize_url(url)
        assert result in ("https://example.com/post", "https://example.com/post?")

    def test_invalid_url_returned_as_is(self):
        bad = "not-a-url-at-all"
        result = normalize_url(bad)
        assert isinstance(result, str)


class TestUrlToSlug:
    def test_basic_path(self):
        url = "https://example.com/blog/how-to-scrape"
        slug = url_to_slug(url)
        assert slug == "blog-how-to-scrape"

    def test_trailing_slash_stripped(self):
        url = "https://example.com/blog/my-post/"
        slug = url_to_slug(url)
        assert not slug.endswith("-")

    def test_root_url_uses_domain(self):
        url = "https://example.com/"
        slug = url_to_slug(url)
        assert "example" in slug

    def test_slug_length_limit(self):
        url = "https://example.com/" + "a" * 200
        slug = url_to_slug(url)
        assert len(slug) <= 100

    def test_special_chars_replaced(self):
        url = "https://example.com/blog/post?id=1&ref=home"
        slug = url_to_slug(url)
        assert "?" not in slug
        assert "=" not in slug
        assert "&" not in slug

    def test_numeric_path(self):
        url = "https://example.com/2024/01/my-post"
        slug = url_to_slug(url)
        assert "2024" in slug
        assert "01" in slug
        assert "my-post" in slug


class TestIsNonContentUrl:
    def test_image_extensions(self):
        assert is_non_content_url("https://example.com/image.jpg") is True
        assert is_non_content_url("https://example.com/img.PNG") is True
        assert is_non_content_url("https://example.com/photo.webp") is True

    def test_css_js(self):
        assert is_non_content_url("https://example.com/style.css") is True
        assert is_non_content_url("https://example.com/app.js") is True

    def test_html_is_content(self):
        assert is_non_content_url("https://example.com/post.html") is False

    def test_no_extension_is_content(self):
        assert is_non_content_url("https://example.com/blog/my-post") is False

    def test_pdf_is_non_content(self):
        assert is_non_content_url("https://example.com/report.pdf") is True


class TestExtractDomain:
    def test_basic(self):
        assert extract_domain("https://example.com/blog") == "example.com"

    def test_www(self):
        assert extract_domain("https://www.example.com/blog") == "www.example.com"

    def test_lowercased(self):
        assert extract_domain("https://EXAMPLE.COM/blog") == "example.com"

    def test_with_port(self):
        assert extract_domain("https://example.com:8443/blog") == "example.com:8443"
