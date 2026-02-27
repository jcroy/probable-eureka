"""Tests for webcollector.extractor.metadata."""

from datetime import date

from webcollector.extractor.metadata import DocumentMetadata, extract_metadata


class TestExtractMetadata:
    def test_from_html_metadata(self):
        meta = extract_metadata({
            "title": "My Document",
            "author": "Jane Smith",
            "published_date": "2026-03-15",
            "language": "en",
        })
        assert meta.title == "My Document"
        assert meta.author == "Jane Smith"
        assert meta.published_date == date(2026, 3, 15)
        assert meta.language == "en"

    def test_date_from_text(self):
        meta = extract_metadata(
            {},
            text="Published on 2026-01-20. This is an article.",
        )
        assert meta.published_date == date(2026, 1, 20)

    def test_no_date_found(self):
        meta = extract_metadata({}, text="No date here at all.")
        assert meta.published_date is None

    def test_date_formats(self):
        for raw, expected in [
            ("2026-03-15", date(2026, 3, 15)),
            ("March 15, 2026", date(2026, 3, 15)),
            ("03/15/2026", date(2026, 3, 15)),
        ]:
            meta = extract_metadata({"published_date": raw})
            assert meta.published_date == expected, f"Failed for: {raw}"

    def test_language_detection(self):
        meta = extract_metadata(
            {},
            text=(
                "This is a reasonably long English text that should be "
                "detected as English by the language detection library."
            ),
        )
        assert meta.language == "en"

    def test_to_dict(self):
        meta = DocumentMetadata(
            title="Test",
            author="Author",
            published_date=date(2026, 1, 1),
            language="en",
        )
        d = meta.to_dict()
        assert d["title"] == "Test"
        assert d["published_date"] == "2026-01-01"
