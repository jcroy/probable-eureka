"""Safe file path generation and filesystem utilities."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


def content_hash(data: bytes) -> str:
    """Compute SHA-256 hex digest of content."""
    return hashlib.sha256(data).hexdigest()


def content_addressed_path(hash_hex: str, extension: str = "") -> str:
    """Generate a content-addressed relative path: {prefix2}/{hash}.{ext}

    Example: "a1/a1b2c3d4...ef.pdf"
    """
    prefix = hash_hex[:2]
    if extension and not extension.startswith("."):
        extension = "." + extension
    return f"{prefix}/{hash_hex}{extension}"


def safe_filename(url: str, max_length: int = 200) -> str:
    """Derive a safe filename from a URL."""
    parsed = urlparse(url)
    name = parsed.path.split("/")[-1] or "index"
    # Strip unsafe characters
    name = re.sub(r'[^\w\-.]', '_', name)
    return name[:max_length]


def extension_from_content_type(content_type: str) -> str:
    """Map a MIME content type to a file extension."""
    mapping = {
        "text/html": ".html",
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/csv": ".csv",
        "text/plain": ".txt",
        "application/xml": ".xml",
        "text/xml": ".xml",
    }
    # Strip parameters (e.g., "text/html; charset=utf-8" -> "text/html")
    base_type = content_type.split(";")[0].strip().lower()
    return mapping.get(base_type, "")


def ensure_dir(path: Path) -> Path:
    """Create directory (and parents) if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)
    return path
