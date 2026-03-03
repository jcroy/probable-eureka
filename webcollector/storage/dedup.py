"""Content deduplication using SHA-256 exact match and simhash near-duplicate detection.

Two levels of dedup:
1. Exact: SHA-256 of extracted text — catches byte-identical content.
2. Near-duplicate: Simhash (64-bit locality-sensitive hash) — catches pages
   with minor differences (different headers/footers, date stamps, etc.).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter


def content_hash_text(text: str) -> str:
    """SHA-256 hash of normalized text for exact dedup."""
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Sentinel value for empty text - ensures no false positive matches
EMPTY_TEXT_SIMHASH = -1


def simhash(text: str, hash_bits: int = 64) -> int:
    """Compute a simhash fingerprint of the text.

    Uses word-level 3-grams (shingles) as features, weighted by frequency.
    Returns an integer fingerprint that can be compared with hamming distance.
    Returns EMPTY_TEXT_SIMHASH (-1) for empty text to prevent false positive matches.
    """
    tokens = _tokenize(text)
    if not tokens:
        return EMPTY_TEXT_SIMHASH

    # Build shingles (3-grams)
    shingles = []
    for i in range(len(tokens) - 2):
        shingles.append(" ".join(tokens[i : i + 3]))

    if not shingles:
        # Text too short for 3-grams, use individual tokens
        shingles = tokens

    counts = Counter(shingles)

    # Weighted bit vector
    v = [0] * hash_bits
    for shingle, weight in counts.items():
        h = _hash_to_bits(shingle, hash_bits)
        for i in range(hash_bits):
            if h & (1 << i):
                v[i] += weight
            else:
                v[i] -= weight

    # Construct fingerprint
    fingerprint = 0
    for i in range(hash_bits):
        if v[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def simhash_hex(text: str, hash_bits: int = 64) -> str:
    """Return simhash as a hex string."""
    h = simhash(text, hash_bits)
    hex_digits = hash_bits // 4
    return format(h, f"0{hex_digits}x")


def hamming_distance(a: int, b: int) -> int:
    """Count the number of differing bits between two integers."""
    return bin(a ^ b).count("1")


def is_near_duplicate(
    hash_a: int,
    hash_b: int,
    threshold: int = 3,
) -> bool:
    """Check if two simhash fingerprints are near-duplicates.

    Default threshold of 3 bits (out of 64) means documents must share
    ~95% structural similarity to be considered duplicates.
    Returns False if either hash is the empty text sentinel.
    """
    # Never match empty text against anything (prevents false positives)
    if hash_a == EMPTY_TEXT_SIMHASH or hash_b == EMPTY_TEXT_SIMHASH:
        return False
    return hamming_distance(hash_a, hash_b) <= threshold


class DedupChecker:
    """Stateful dedup checker that tracks seen content within a run.

    Maintains in-memory sets for fast lookup during a single crawl run.
    For cross-run dedup, use the database queries instead.
    """

    def __init__(self, simhash_threshold: int = 3) -> None:
        self._seen_hashes: set[str] = set()
        self._simhashes: list[tuple[str, int]] = []  # (doc_id, simhash)
        self._threshold = simhash_threshold

    def check_exact(self, text_hash: str) -> bool:
        """Return True if this exact content hash has been seen."""
        return text_hash in self._seen_hashes

    def record_hash(self, text_hash: str) -> None:
        """Record a content hash as seen."""
        self._seen_hashes.add(text_hash)

    def check_near_duplicate(self, text: str) -> str | None:
        """Check if text is a near-duplicate of any seen document.

        Returns the doc_id of the near-duplicate, or None.
        """
        h = simhash(text)
        for doc_id, existing_hash in self._simhashes:
            if is_near_duplicate(h, existing_hash, self._threshold):
                return doc_id
        return None

    def record_simhash(self, doc_id: str, text: str) -> str:
        """Record a document's simhash. Returns the hex simhash."""
        h = simhash(text)
        self._simhashes.append((doc_id, h))
        return simhash_hex(text)


# ── Internal helpers ───────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALPHA_RE = re.compile(r"[^a-z0-9\s]")


def _normalize_text(text: str) -> str:
    """Normalize text for hashing: lowercase, collapse whitespace, strip punctuation."""
    text = text.lower()
    text = _NON_ALPHA_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def _tokenize(text: str) -> list[str]:
    """Simple whitespace tokenizer on normalized text."""
    return _normalize_text(text).split()


def _hash_to_bits(s: str, bits: int = 64) -> int:
    """Hash a string to an integer with the given number of bits."""
    h = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(h, 16) % (2**bits)
