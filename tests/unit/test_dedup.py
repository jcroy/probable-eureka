"""Tests for webcollector.storage.dedup."""

from webcollector.storage.dedup import (
    DedupChecker,
    content_hash_text,
    hamming_distance,
    is_near_duplicate,
    simhash,
    simhash_hex,
)


class TestContentHashText:
    def test_identical_text_same_hash(self):
        h1 = content_hash_text("Hello world")
        h2 = content_hash_text("Hello world")
        assert h1 == h2

    def test_different_text_different_hash(self):
        h1 = content_hash_text("Hello world")
        h2 = content_hash_text("Goodbye world")
        assert h1 != h2

    def test_normalization(self):
        """Whitespace and case differences should produce the same hash."""
        h1 = content_hash_text("Hello  World")
        h2 = content_hash_text("hello world")
        assert h1 == h2


class TestSimhash:
    def test_returns_integer(self):
        result = simhash("The quick brown fox jumps over the lazy dog")
        assert isinstance(result, int)

    def test_empty_text(self):
        assert simhash("") == 0

    def test_similar_texts_close_hashes(self):
        text_a = (
            "The quick brown fox jumps over the lazy dog "
            "and runs through the green field on a sunny day"
        )
        text_b = (
            "The quick brown fox jumps over the lazy dog "
            "and runs through the green meadow on a sunny day"
        )
        text_c = "Quantum physics explores the fundamental nature of reality"

        dist_ab = hamming_distance(simhash(text_a), simhash(text_b))
        dist_ac = hamming_distance(simhash(text_a), simhash(text_c))
        assert dist_ab < dist_ac

    def test_hex_output_length(self):
        h = simhash_hex("some text here for testing purposes")
        assert len(h) == 16  # 64 bits / 4 = 16 hex chars


class TestHammingDistance:
    def test_identical(self):
        assert hamming_distance(0b1010, 0b1010) == 0

    def test_one_bit(self):
        assert hamming_distance(0b1010, 0b1011) == 1

    def test_all_different(self):
        assert hamming_distance(0b0000, 0b1111) == 4


class TestIsNearDuplicate:
    def test_identical_is_duplicate(self):
        assert is_near_duplicate(42, 42) is True

    def test_close_is_duplicate(self):
        assert is_near_duplicate(0b1010, 0b1011, threshold=1) is True

    def test_far_is_not_duplicate(self):
        assert is_near_duplicate(0b0000, 0b1111, threshold=2) is False


class TestDedupChecker:
    def test_exact_dedup(self):
        checker = DedupChecker()
        checker.record_hash("abc123")
        assert checker.check_exact("abc123") is True
        assert checker.check_exact("def456") is False

    def test_record_simhash(self):
        checker = DedupChecker()
        h = checker.record_simhash("doc-1", "some text for testing")
        assert isinstance(h, str)
        assert len(h) == 16
