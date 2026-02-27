"""Tests for webcollector.utils.url_utils."""

from webcollector.utils.url_utils import (
    get_domain,
    is_document_url,
    is_same_domain,
    normalize_url,
    resolve_url,
    url_matches_patterns,
)


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self):
        # Path is case-sensitive per RFC, only scheme+host are lowered
        assert normalize_url("HTTP://Example.COM/Page") == "http://example.com/Page"

    def test_strips_fragment(self):
        assert normalize_url("http://example.com/page#section") == "http://example.com/page"

    def test_strips_trailing_slash(self):
        assert normalize_url("http://example.com/page/") == "http://example.com/page"

    def test_sorts_query_params(self):
        result = normalize_url("http://example.com/page?b=2&a=1")
        assert result == "http://example.com/page?a=1&b=2"

    def test_removes_tracking_params(self):
        result = normalize_url("http://example.com/page?utm_source=google&id=5")
        assert "utm_source" not in result
        assert "id=5" in result


class TestGetDomain:
    def test_extracts_domain(self):
        assert get_domain("http://example.com/page") == "example.com"

    def test_extracts_subdomain(self):
        assert get_domain("https://www.example.com/page") == "www.example.com"


class TestIsDocumentUrl:
    def test_pdf(self):
        assert is_document_url("http://example.com/file.pdf") is True

    def test_docx(self):
        assert is_document_url("http://example.com/file.docx") is True

    def test_html(self):
        assert is_document_url("http://example.com/page.html") is False

    def test_no_extension(self):
        assert is_document_url("http://example.com/page") is False

    def test_pdf_with_query(self):
        assert is_document_url("http://example.com/file.pdf?v=1") is True


class TestResolveUrl:
    def test_absolute_url(self):
        result = resolve_url("http://example.com/page", "http://other.com/file")
        assert result == "http://other.com/file"

    def test_relative_url(self):
        result = resolve_url("http://example.com/dir/page", "other.html")
        assert result == "http://example.com/dir/other.html"

    def test_root_relative(self):
        result = resolve_url("http://example.com/dir/page", "/root.html")
        assert result == "http://example.com/root.html"


class TestUrlMatchesPatterns:
    def test_matches_pattern(self):
        assert url_matches_patterns(
            "http://example.com/docs/api", [r"docs/"]
        ) is True

    def test_no_match(self):
        assert url_matches_patterns(
            "http://example.com/blog/post", [r"docs/"]
        ) is False

    def test_empty_patterns(self):
        assert url_matches_patterns("http://example.com/page", []) is False


class TestIsSameDomain:
    def test_same(self):
        # is_same_domain takes a URL and a domain string
        assert is_same_domain("http://example.com/a", "example.com") is True

    def test_subdomain(self):
        assert is_same_domain("http://www.example.com/a", "example.com") is True

    def test_different(self):
        assert is_same_domain("http://example.com/a", "other.com") is False
