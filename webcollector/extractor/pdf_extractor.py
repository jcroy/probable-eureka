"""PDF text extraction using Mistral OCR API with pdfplumber local fallback.

Primary: Mistral OCR API — sends document URL or base64 data to Mistral's
         OCR model and gets back markdown text per page.
Fallback: pdfplumber — local extraction for when the API is unavailable or
          the document is behind auth.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from webcollector.config import ExtractionConfig

logger = structlog.get_logger(__name__)


class PDFExtractor:
    """Extract text from PDFs using Mistral OCR or local pdfplumber."""

    def __init__(self, config: ExtractionConfig) -> None:
        self._config = config
        self._client = None

    def _get_mistral_client(self):
        """Lazily initialize the Mistral client."""
        if self._client is None:
            api_key = self._config.mistral_api_key
            if not api_key:
                raise ValueError(
                    f"Mistral API key not found in env var "
                    f"'{self._config.mistral_api_key_env}'"
                )
            from mistralai import Mistral

            self._client = Mistral(api_key=api_key)
        return self._client

    async def extract_from_url(self, url: str) -> PDFExtractionResult:
        """Extract text from a PDF at the given URL.

        Tries Mistral OCR API first, falls back to local extraction
        if the API call fails.
        """
        if self._config.pdf_provider == "mistral":
            try:
                return await self._extract_mistral_url(url)
            except Exception:
                logger.warning(
                    "mistral_ocr_failed_falling_back",
                    url=url,
                    exc_info=True,
                )

        # Fallback: we can't locally extract from a URL directly,
        # so return empty with a note
        logger.warning("pdf_url_extraction_no_local_fallback", url=url)
        return PDFExtractionResult(
            text="",
            page_count=0,
            method="none",
            error="Local extraction requires a file path, not a URL",
        )

    async def extract_from_file(self, file_path: Path) -> PDFExtractionResult:
        """Extract text from a local PDF file.

        Tries Mistral OCR API (via base64 upload) first, falls back
        to pdfplumber for local extraction.
        """
        if not file_path.exists():
            return PDFExtractionResult(
                text="", page_count=0, method="none", error="File not found"
            )

        if self._config.pdf_provider == "mistral":
            try:
                return await self._extract_mistral_file(file_path)
            except Exception:
                logger.warning(
                    "mistral_ocr_file_failed_falling_back",
                    path=str(file_path),
                    exc_info=True,
                )

        # Local fallback with pdfplumber
        return self._extract_local(file_path)

    async def _extract_mistral_url(self, url: str) -> PDFExtractionResult:
        """Call Mistral OCR API with a document URL."""
        client = self._get_mistral_client()

        response = await client.ocr.process_async(
            model=self._config.mistral_model,
            document={
                "type": "document_url",
                "document_url": url,
            },
        )

        pages_text = []
        for page in response.pages:
            pages_text.append(page.markdown)

        full_text = "\n\n".join(pages_text)

        logger.info(
            "mistral_ocr_completed",
            url=url,
            pages=len(response.pages),
            text_length=len(full_text),
        )

        return PDFExtractionResult(
            text=full_text,
            page_count=len(response.pages),
            method="mistral_ocr",
        )

    async def _extract_mistral_file(self, file_path: Path) -> PDFExtractionResult:
        """Call Mistral OCR API with base64-encoded file data."""
        # Check file size to prevent OOM on large PDFs
        # base64 expands by ~33%, so 100MB PDF becomes ~133MB in memory
        max_size = self._config.max_pdf_size_bytes
        file_size = file_path.stat().st_size
        if file_size > max_size:
            logger.warning(
                "pdf_too_large_for_ocr",
                path=str(file_path),
                size=file_size,
                max_size=max_size,
            )
            # Fall back to local extraction for large files
            return self._extract_local(file_path)

        client = self._get_mistral_client()

        file_data = file_path.read_bytes()
        b64_data = base64.b64encode(file_data).decode("utf-8")

        # Upload as base64 document
        from mistralai import DocumentURLChunk

        response = await client.ocr.process_async(
            model=self._config.mistral_model,
            document=DocumentURLChunk(
                document_url=f"data:application/pdf;base64,{b64_data}",
            ),
        )

        pages_text = []
        for page in response.pages:
            pages_text.append(page.markdown)

        full_text = "\n\n".join(pages_text)

        logger.info(
            "mistral_ocr_file_completed",
            path=str(file_path),
            pages=len(response.pages),
            text_length=len(full_text),
        )

        return PDFExtractionResult(
            text=full_text,
            page_count=len(response.pages),
            method="mistral_ocr",
        )

    def _extract_local(self, file_path: Path) -> PDFExtractionResult:
        """Extract text from a PDF using pdfplumber (local, no API)."""
        try:
            import pdfplumber

            pages_text = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    pages_text.append(text)

            full_text = "\n\n".join(pages_text)

            logger.info(
                "pdfplumber_extraction_completed",
                path=str(file_path),
                pages=len(pages_text),
                text_length=len(full_text),
            )

            return PDFExtractionResult(
                text=full_text,
                page_count=len(pages_text),
                method="pdfplumber",
            )

        except Exception:
            logger.error(
                "pdfplumber_extraction_failed",
                path=str(file_path),
                exc_info=True,
            )
            return PDFExtractionResult(
                text="",
                page_count=0,
                method="pdfplumber",
                error="Local PDF extraction failed",
            )


class PDFExtractionResult:
    """Result of PDF text extraction."""

    def __init__(
        self,
        text: str,
        page_count: int = 0,
        method: str = "",
        error: str | None = None,
    ) -> None:
        self.text = text
        self.page_count = page_count
        self.method = method
        self.error = error
