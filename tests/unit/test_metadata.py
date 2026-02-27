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

    def test_date_from_url_clerkbase(self):
        """Clerkbase-style URLs like nov24_25tc.htm → 2025-11-24."""
        meta = extract_metadata(
            {},
            text="No date in text.",
            url="https://clerkshq.com/Content/SouthKingstown-ri/council/2025/nov24_25tc.htm",
        )
        assert meta.published_date == date(2025, 11, 24)

    def test_date_from_url_various_months(self):
        """Clerkbase-style month abbreviation filenames."""
        for filename, expected in [
            ("jan15_26tc.htm", date(2026, 1, 15)),
            ("sep08_25tc.htm", date(2025, 9, 8)),
            ("oct27_25tc.htm", date(2025, 10, 27)),
            ("dec01_24.htm", date(2024, 12, 1)),
        ]:
            meta = extract_metadata(
                {}, text="No date.", url=f"https://example.com/{filename}"
            )
            assert meta.published_date == expected, f"Failed for: {filename}"

    def test_date_from_url_wordpress_style(self):
        """WordPress/blog-style: /YYYY/MM/DD/slug."""
        for url, expected in [
            ("https://blog.com/2025/11/24/my-article", date(2025, 11, 24)),
            ("https://news.com/2026/1/5/headline-here", date(2026, 1, 5)),
            ("https://site.com/archive/2025/03/15/post.html", date(2025, 3, 15)),
        ]:
            meta = extract_metadata({}, text="No date.", url=url)
            assert meta.published_date == expected, f"Failed for: {url}"

    def test_date_from_url_jekyll_style(self):
        """Jekyll/Hugo-style: /YYYY-MM-DD-slug."""
        for url, expected in [
            ("https://blog.com/2025-11-24-my-post", date(2025, 11, 24)),
            ("https://site.com/posts/2026-01-05-title", date(2026, 1, 5)),
        ]:
            meta = extract_metadata({}, text="No date.", url=url)
            assert meta.published_date == expected, f"Failed for: {url}"

    def test_date_from_url_compact_iso(self):
        """Compact 8-digit ISO in filename: /20251124-article.html."""
        for url, expected in [
            ("https://example.com/20251124-report.html", date(2025, 11, 24)),
            ("https://example.com/docs/20260105.pdf", date(2026, 1, 5)),
        ]:
            meta = extract_metadata({}, text="No date.", url=url)
            assert meta.published_date == expected, f"Failed for: {url}"

    def test_date_from_url_guardian_style(self):
        """Guardian-style: /YYYY/mon/DD/slug."""
        for url, expected in [
            ("https://guardian.com/2025/nov/24/headline", date(2025, 11, 24)),
            ("https://news.com/2026/jan/5/story", date(2026, 1, 5)),
        ]:
            meta = extract_metadata({}, text="No date.", url=url)
            assert meta.published_date == expected, f"Failed for: {url}"

    def test_url_date_does_not_override_html_meta(self):
        """HTML meta tag date should take priority over URL date."""
        meta = extract_metadata(
            {"published_date": "2026-03-15"},
            text="",
            url="https://example.com/nov24_25tc.htm",
        )
        assert meta.published_date == date(2026, 3, 15)

    def test_url_no_false_positive_on_random_numbers(self):
        """URLs with numbers that aren't dates shouldn't produce false positives."""
        for url in [
            "https://example.com/product/12345",
            "https://example.com/page?id=999",
            "https://example.com/v2/api/users",
        ]:
            meta = extract_metadata({}, text="No date.", url=url)
            # These shouldn't extract a date from the URL
            # (text fallback "No date." also won't match)
            assert meta.published_date is None, f"False positive for: {url}"

    def test_prose_date_in_text(self):
        """Government minutes style: 'the 8th day of September 2025'."""
        meta = extract_metadata(
            {},
            text="held at the Town Hall on the 8th day of September 2025 at 7:38 PM.",
        )
        assert meta.published_date == date(2025, 9, 8)

    def test_prose_date_variations(self):
        for text, expected in [
            ("the 1st day of January 2026", date(2026, 1, 1)),
            ("the 22nd day of February 2025", date(2025, 2, 22)),
            ("the 3rd day of March 2025", date(2025, 3, 3)),
            ("the 14 day of October 2025", date(2025, 10, 14)),
        ]:
            meta = extract_metadata({}, text=text)
            assert meta.published_date == expected, f"Failed for: {text}"

    def test_prose_date_preferred_over_generic_regex(self):
        """Prose date should win over a random earlier date in the text."""
        text = (
            "VOTED: that the minutes of August 12, 2025 are accepted.\n"
            "At a session held on the 8th day of September 2025."
        )
        meta = extract_metadata({}, text=text)
        assert meta.published_date == date(2025, 9, 8)

    def test_url_date_preferred_over_text_dates(self):
        """URL date should win when text contains misleading dates."""
        text = (
            "VOTED: that the minutes of August 12, 2025 are accepted.\n"
            "At a session held on the 8th day of September 2025."
        )
        meta = extract_metadata(
            {}, text=text, url="https://example.com/sep08_25tc.htm"
        )
        assert meta.published_date == date(2025, 9, 8)

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
