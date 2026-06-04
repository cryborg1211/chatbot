"""Document loaders — turn raw bytes into a list of LlamaIndex Documents.

Dispatch by MIME type:
  - application/pdf  → pypdf
  - .docx            → python-docx
  - text/plain       → utf-8 decode

We deliberately use the low-level libraries directly (instead of
`llama_index.readers.file`) because we already have the bytes in memory —
no need for the LlamaIndex readers' temp-file roundtrip.
"""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LoaderError(Exception):
    """Raised when the upload can't be parsed into usable text.

    Caller maps this to HTTP 422 with ``error_code = "PARSE_ERROR"``.
    """


def load_documents(file_bytes: bytes, mime_type: str, original_name: str) -> list[Any]:
    """Returns a list of :class:`llama_index.core.Document` objects.

    Empty files / no-text PDFs raise :class:`LoaderError`.
    """
    if not file_bytes:
        raise LoaderError("Uploaded file is empty.")

    if mime_type == "text/plain":
        return _load_text(file_bytes, original_name)

    if mime_type == "application/pdf":
        return _load_pdf(file_bytes, original_name)

    if mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return _load_docx(file_bytes, original_name)

    raise LoaderError(f"Unsupported MIME type: {mime_type}")


# ---------------------------------------------------------------------
#  Per-format loaders
# ---------------------------------------------------------------------

def _load_text(file_bytes: bytes, original_name: str) -> list[Any]:
    from llama_index.core import Document

    text = file_bytes.decode("utf-8", errors="replace")
    if not text.strip():
        raise LoaderError("TXT file has no readable content.")
    return [Document(text=text, metadata={"source": original_name})]


def _load_pdf(file_bytes: bytes, original_name: str) -> list[Any]:
    from llama_index.core import Document
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        raise LoaderError(f"PDF parse failed: {exc}") from exc

    pages: list[Any] = []
    for idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("pdf_page_extract_failed page=%s err=%s", idx, exc)
            text = ""
        if text.strip():
            pages.append(
                Document(
                    text=text,
                    metadata={"source": original_name, "page": idx + 1},
                )
            )

    if not pages:
        raise LoaderError("PDF contains no extractable text (likely scanned — OCR not enabled).")
    return pages


def _load_docx(file_bytes: bytes, original_name: str) -> list[Any]:
    from docx import Document as DocxDocument  # python-docx
    from llama_index.core import Document

    try:
        docx_obj = DocxDocument(io.BytesIO(file_bytes))
    except Exception as exc:
        raise LoaderError(f"DOCX parse failed: {exc}") from exc

    paragraphs = [p.text for p in docx_obj.paragraphs if p.text and p.text.strip()]
    if not paragraphs:
        raise LoaderError("DOCX has no readable text.")

    return [
        Document(
            text="\n".join(paragraphs),
            metadata={"source": original_name},
        )
    ]
