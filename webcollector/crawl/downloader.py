"""Streaming file downloader for binary documents (PDF, DOCX, etc.).

Crawlee's handlers are optimized for HTML pages. For binary file downloads,
we use httpx directly with streaming to write files to disk efficiently.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog

from webcollector.crawl.rate_limiter import DomainRateLimiter
from webcollector.utils.file_utils import (
    content_addressed_path,
    ensure_dir,
    extension_from_content_type,
)
from webcollector.utils.hashing import sha256_hex
from webcollector.utils.url_utils import get_domain

logger = structlog.get_logger(__name__)


class DownloadResult:
    """Result of a file download."""

    def __init__(
        self,
        url: str,
        file_path: str,
        content_hash: str,
        content_type: str,
        file_size: int,
        status_code: int,
    ) -> None:
        self.url = url
        self.file_path = file_path
        self.content_hash = content_hash
        self.content_type = content_type
        self.file_size = file_size
        self.status_code = status_code


class FileDownloader:
    """Download binary files (PDFs, DOCX, etc.) via httpx streaming."""

    def __init__(
        self,
        store_dir: Path,
        rate_limiter: DomainRateLimiter,
        user_agent: str = "webcollector/0.1",
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        self._store_dir = store_dir
        self._rate_limiter = rate_limiter
        self._user_agent = user_agent
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self._user_agent},
                timeout=httpx.Timeout(self._timeout, connect=30.0),
                follow_redirects=True,
                max_redirects=5,
            )
        return self._client

    async def download(self, url: str) -> DownloadResult | None:
        """Download a file and store it content-addressed.

        Returns DownloadResult on success, None on failure after retries.
        """
        domain = get_domain(url)

        for attempt in range(1, self._max_retries + 1):
            try:
                await self._rate_limiter.acquire(domain)
                client = await self._get_client()

                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "application/octet-stream")
                    chunks: list[bytes] = []

                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        chunks.append(chunk)

                    data = b"".join(chunks)

                hash_hex = sha256_hex(data)
                ext = extension_from_content_type(content_type)
                if not ext:
                    # Try to get extension from URL
                    url_path = url.rsplit("/", 1)[-1].rsplit("?", 1)[0]
                    if "." in url_path:
                        ext = "." + url_path.rsplit(".", 1)[-1].lower()

                rel_path = content_addressed_path(hash_hex, ext.lstrip(".") if ext else "bin")
                abs_path = self._store_dir / "raw" / rel_path
                ensure_dir(abs_path.parent)
                abs_path.write_bytes(data)

                logger.info(
                    "file_downloaded",
                    url=url,
                    path=rel_path,
                    size=len(data),
                    content_type=content_type,
                )

                return DownloadResult(
                    url=url,
                    file_path=rel_path,
                    content_hash=hash_hex,
                    content_type=content_type,
                    file_size=len(data),
                    status_code=response.status_code,
                )

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    logger.warning(
                        "download_retry",
                        url=url,
                        status=status,
                        attempt=attempt,
                    )
                    import asyncio

                    await asyncio.sleep(2**attempt)
                    continue
                logger.error("download_failed", url=url, status=status)
                return None

            except (httpx.RequestError, OSError) as e:
                if attempt < self._max_retries:
                    logger.warning(
                        "download_retry",
                        url=url,
                        error=str(e),
                        attempt=attempt,
                    )
                    import asyncio

                    await asyncio.sleep(2**attempt)
                    continue
                logger.error("download_failed", url=url, error=str(e))
                return None

        return None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
