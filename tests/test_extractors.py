"""Unit tests for extraction modules."""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

class TestMetadataExtraction:
    def test_jsonld_title(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html, "https://example.com/blog/how-to-extract-structured-content-for-llms")
        assert meta["title"] == "How to Extract Structured Content for LLMs"

    def test_jsonld_author(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["author"] == "Jane Smith"

    def test_og_site_name(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["site_name"] == "Tech Blog"

    def test_published_at_parsed(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["published_at"] is not None
        assert "2024-01-15" in meta["published_at"]

    def test_canonical_url(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["canonical_url"] == "https://example.com/blog/how-to-extract-structured-content-for-llms"

    def test_tags_from_og_and_jsonld(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        tags = meta["tags"]
        assert isinstance(tags, list)
        # article:tag → python, web-scraping; keywords → python, web-scraping, scrapy
        assert any(t.lower() in ("python", "web-scraping", "scrapy") for t in tags)

    def test_language_from_html_tag(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["language"] == "en"

    def test_og_image(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        images = meta["images"]
        assert len(images) >= 1
        assert images[0]["url"] == "https://example.com/img/diagram.png"

    def test_summary_from_description(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        assert meta["summary"] is not None
        assert "llm" in meta["summary"].lower() or "content" in meta["summary"].lower()

    def test_raw_metadata_structure(self, article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(article_html)
        raw = meta["raw_metadata"]
        assert "jsonld" in raw
        assert "og" in raw
        assert "twitter" in raw
        assert raw["jsonld"].get("@type") == "Article"

    def test_minimal_html_no_crash(self, minimal_article_html):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata(minimal_article_html)
        assert isinstance(meta, dict)
        assert "title" in meta

    def test_empty_html_returns_defaults(self):
        from llmparser.extractors.metadata import extract_metadata

        meta = extract_metadata("")
        assert meta["title"] == ""
        assert meta["tags"] == []
        assert meta["author"] is None


# ---------------------------------------------------------------------------
# Heuristics: article scoring
# ---------------------------------------------------------------------------

class TestArticleScoring:
    def test_article_scores_high(self, article_html):
        from llmparser.extractors.heuristics import ARTICLE_SCORE_THRESHOLD, Heuristics

        h = Heuristics()
        score = h.article_score("https://example.com/blog/how-to-extract-structured-content-for-llms", article_html)
        assert score >= ARTICLE_SCORE_THRESHOLD, f"Expected score >= {ARTICLE_SCORE_THRESHOLD}, got {score}"

    def test_listing_page_scores_low(self, listing_html):
        from llmparser.extractors.heuristics import ARTICLE_SCORE_THRESHOLD, Heuristics

        h = Heuristics()
        score = h.article_score("https://example.com/blog", listing_html)
        assert score < ARTICLE_SCORE_THRESHOLD, f"Expected low score, got {score}"

    def test_tag_page_heavily_penalised(self, listing_html):
        from llmparser.extractors.heuristics import Heuristics

        h = Heuristics()
        score = h.article_score("https://example.com/tag/python", listing_html)
        assert score < 10, f"Tag page should score < 10, got {score}"

    def test_minimal_article_passes_threshold(self, minimal_article_html):
        from llmparser.extractors.heuristics import ARTICLE_SCORE_THRESHOLD, Heuristics

        h = Heuristics()
        score = h.article_score("https://example.com/posts/a-simple-post", minimal_article_html)
        assert score >= ARTICLE_SCORE_THRESHOLD, f"Minimal article should pass: got {score}"

    def test_reading_time_minimum_one(self):
        from llmparser.extractors.heuristics import Heuristics

        h = Heuristics()
        assert h.reading_time(0) == 1
        assert h.reading_time(100) == 1
        assert h.reading_time(200) == 1
        assert h.reading_time(201) == 2
        assert h.reading_time(1000) == 5


# ---------------------------------------------------------------------------
# Heuristics: JS detection
# ---------------------------------------------------------------------------

class TestJsDetection:
    def test_jsapp_detected(self, jsapp_html):
        from llmparser.extractors.heuristics import Heuristics

        h = Heuristics()
        assert h.needs_js(jsapp_html) is True

    def test_static_article_not_detected(self, article_html):
        from llmparser.extractors.heuristics import Heuristics

        h = Heuristics()
        assert h.needs_js(article_html) is False

    def test_enable_js_message_detected(self):
        from llmparser.extractors.heuristics import Heuristics

        html = "<html><body><p>Please enable JavaScript to view this site.</p></body></html>"
        h = Heuristics()
        assert h.needs_js(html) is True

    def test_empty_html_returns_false(self):
        from llmparser.extractors.heuristics import Heuristics

        h = Heuristics()
        assert h.needs_js("") is False


# ---------------------------------------------------------------------------
# Main content extraction
# ---------------------------------------------------------------------------

class TestMainContentExtraction:
    def test_readability_extracts_article(self, article_html):
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(article_html, "https://example.com/blog/post")
        assert result.word_count >= 50
        assert result.method in ("readability", "trafilatura", "dom_heuristic")
        assert len(result.html) > 100

    def test_minimal_article_extracts_content(self, minimal_article_html):
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(minimal_article_html, "https://example.com/posts/simple")
        assert result.word_count >= 20
        assert len(result.html) > 50

    def test_dom_heuristic_fallback(self):
        """Verify DOM heuristic works when readability/trafilatura would get sparse results."""
        from llmparser.extractors.main_content import dom_heuristic_extract

        html = """
        <html><body>
          <div class="article-content">
            <p>Paragraph one with enough content to count.</p>
            <p>Paragraph two adds more words and context to the article.</p>
            <p>Paragraph three ensures we have enough density.</p>
          </div>
        </body></html>
        """
        result = dom_heuristic_extract(html)
        assert "Paragraph" in result


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------

class TestContentBlocks:
    def test_headings_extracted(self, article_html):
        from llmparser.extractors.blocks import html_to_blocks
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(article_html)
        blocks = html_to_blocks(result.html)
        headings = [b for b in blocks if b["type"] == "heading"]
        assert len(headings) >= 1

    def test_code_block_language(self, article_html):
        from llmparser.extractors.blocks import html_to_blocks
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(article_html)
        blocks = html_to_blocks(result.html)
        code_blocks = [b for b in blocks if b["type"] == "code"]
        if code_blocks:
            assert code_blocks[0].get("language") in ("python", "")

    def test_list_block(self, article_html):
        from llmparser.extractors.blocks import html_to_blocks
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(article_html)
        blocks = html_to_blocks(result.html)
        list_blocks = [b for b in blocks if b["type"] == "list"]
        if list_blocks:
            assert isinstance(list_blocks[0]["items"], list)
            assert len(list_blocks[0]["items"]) > 0

    def test_quote_block(self, article_html):
        from llmparser.extractors.blocks import html_to_blocks
        from llmparser.extractors.main_content import extract_main_content

        result = extract_main_content(article_html)
        blocks = html_to_blocks(result.html)
        quote_blocks = [b for b in blocks if b["type"] == "quote"]
        if quote_blocks:
            assert "best" in quote_blocks[0]["text"].lower()


# ---------------------------------------------------------------------------
# Markdown conversion
# ---------------------------------------------------------------------------

class TestMarkdownConversion:
    def test_code_fences_preserved(self, article_html):
        from llmparser.extractors.main_content import extract_main_content
        from llmparser.extractors.markdown import html_to_markdown

        result = extract_main_content(article_html)
        md = html_to_markdown(result.html)
        # Code fences should appear in markdown
        assert "```" in md or "import scrapy" in md

    def test_headings_use_atx_style(self, article_html):
        from llmparser.extractors.main_content import extract_main_content
        from llmparser.extractors.markdown import html_to_markdown

        result = extract_main_content(article_html)
        md = html_to_markdown(result.html)
        assert "#" in md  # ATX-style headings

    def test_no_excessive_blank_lines(self, article_html):
        from llmparser.extractors.main_content import extract_main_content
        from llmparser.extractors.markdown import html_to_markdown

        result = extract_main_content(article_html)
        md = html_to_markdown(result.html)
        assert "\n\n\n" not in md

    def test_article_markdown_format(self):
        from llmparser.extractors.markdown import format_markdown_article

        md = format_markdown_article(
            title="Test Post",
            author="Alice",
            published_at="2024-01-15T10:00:00+00:00",
            tags=["python", "test"],
            summary="A test summary.",
            content_markdown="## Section\n\nContent here.",
        )
        assert md.startswith("# Test Post")
        assert "**Author:** Alice" in md
        assert "**Published:**" in md
        assert "**Tags:** python, test" in md
        assert "> A test summary." in md
        assert "---" in md
