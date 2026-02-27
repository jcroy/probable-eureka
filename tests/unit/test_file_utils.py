"""Tests for webcollector.utils.file_utils."""

from webcollector.utils.file_utils import (
    content_addressed_path,
    content_hash,
    extension_from_content_type,
    safe_filename,
)


class TestContentHash:
    def test_deterministic(self):
        data = b"hello world"
        assert content_hash(data) == content_hash(data)

    def test_different_data(self):
        assert content_hash(b"hello") != content_hash(b"world")


class TestContentAddressedPath:
    def test_format(self):
        result = content_addressed_path("abcdef1234567890", "pdf")
        assert result == "ab/abcdef1234567890.pdf"

    def test_no_extension(self):
        result = content_addressed_path("abcdef1234567890", "")
        assert result == "ab/abcdef1234567890"

    def test_dot_prefix_extension(self):
        result = content_addressed_path("abcdef", ".txt")
        assert result == "ab/abcdef.txt"


class TestSafeFilename:
    def test_basic(self):
        result = safe_filename("http://example.com/path/file.pdf")
        assert result == "file.pdf"

    def test_no_path(self):
        result = safe_filename("http://example.com/")
        assert result == "index"

    def test_strips_unsafe_chars(self):
        result = safe_filename("http://example.com/file name (1).pdf")
        assert " " not in result
        assert "(" not in result


class TestExtensionFromContentType:
    def test_pdf(self):
        assert extension_from_content_type("application/pdf") == ".pdf"

    def test_html(self):
        assert extension_from_content_type("text/html") == ".html"

    def test_html_with_charset(self):
        assert extension_from_content_type("text/html; charset=utf-8") == ".html"

    def test_unknown(self):
        assert extension_from_content_type("application/octet-stream") == ""

    def test_docx(self):
        ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert extension_from_content_type(ct) == ".docx"
