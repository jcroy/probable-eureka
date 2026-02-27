"""Tests for webcollector.extractor.html_extractor."""

from webcollector.extractor.html_extractor import ExtractionResult, HTMLExtractor


class TestHTMLExtractor:
    def setup_method(self):
        self.extractor = HTMLExtractor()

    def test_basic_extraction(self):
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <article>
                <h1>Article Title</h1>
                <p>This is the main content of the article.</p>
            </article>
        </body>
        </html>
        """
        result = self.extractor.extract(html, url="http://example.com")
        assert "main content" in result.text
        assert result.title == "Test Page"

    def test_empty_html(self):
        result = self.extractor.extract("", url="http://example.com")
        assert result.text == ""

    def test_whitespace_only(self):
        result = self.extractor.extract("   \n  ", url="http://example.com")
        assert result.text == ""

    def test_metadata_extraction(self):
        html = """
        <html lang="en">
        <head>
            <title>Meta Test</title>
            <meta name="author" content="John Doe">
            <meta property="article:published_time" content="2026-01-15">
        </head>
        <body><p>Content here.</p></body>
        </html>
        """
        result = self.extractor.extract(html)
        assert result.metadata.get("author") == "John Doe"
        assert result.metadata.get("language") == "en"

    def test_fallback_on_minimal_html(self):
        html = "<p>Just a paragraph.</p>"
        result = self.extractor.extract(html)
        assert isinstance(result, ExtractionResult)
