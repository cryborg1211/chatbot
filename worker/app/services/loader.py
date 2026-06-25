"""Document loaders — turn raw bytes into a list of LlamaIndex Documents.

Dispatch by MIME type:
  - application/pdf  → Docling Markdown
  - .docx            → Docling Markdown
  - text/plain       → utf-8 decode

Docling converts complex documents into Markdown, preserving table structure
better than plain PDF/DOCX text extraction.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class DoclingResult:
    """Carries both the Markdown text and the native DoclingDocument.

    The chunker uses ``docling_doc`` with ``HierarchicalChunker`` for
    structure-aware splitting.  The ``text`` field is kept for logging /
    fallback display.  ``metadata`` is forwarded into every ``TextNode``.
    """

    text: str
    docling_doc: Any
    metadata: dict[str, Any]


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
        return _load_docling(file_bytes, original_name, ".pdf")

    if mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return _load_docling(file_bytes, original_name, ".docx")
    
    if mime_type == "application/msword":
        return _load_legacy_doc(file_bytes, original_name)
    

    raise LoaderError(f"Unsupported MIME type: {mime_type}")

def _load_legacy_doc(file_bytes: bytes, original_name: str) -> list[Any]:
    """Read a legacy binary ``.doc`` (pre-2007 OLE format).

    Pipeline:
        bytes (.doc)  ── LibreOffice headless ──►  .docx  ──►  _load_docx

    Why not call ``mammoth`` directly?  Mammoth only understands the
    OOXML / docx zip format.  The binary OLE ``.doc`` format produces
    ``Could not find file 'word/document.xml'`` — silent dead end.

    The on-disk conversion uses two temp dirs; both are wiped in the
    ``finally`` block on every code path (success, parse error, crash).
    """
    import shutil
    import tempfile
    from pathlib import Path

    from .preprocessing.docx_processor import (
        DocxProcessingError,
        convert_doc_to_docx,
    )

    # Suffix MUST be .doc — LibreOffice picks the import filter by ext.
    src_dir = Path(tempfile.mkdtemp(prefix="ld3_doc_in_"))
    out_dir: Path | None = None
    try:
        # Use a sanitised stem so non-ASCII filenames don't break soffice
        # (LibreOffice is fussy about filename encoding on Windows).
        safe_stem = "input"
        src_path  = src_dir / f"{safe_stem}.doc"
        src_path.write_bytes(file_bytes)

        try:
            converted = convert_doc_to_docx(src_path)
        except DocxProcessingError as exc:
            raise LoaderError(f"DOC → DOCX conversion failed: {exc}") from exc

        out_dir     = converted.parent
        docx_bytes  = converted.read_bytes()
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)
        if out_dir is not None:
            shutil.rmtree(out_dir, ignore_errors=True)

    if not docx_bytes:
        raise LoaderError("LibreOffice produced an empty .docx — corrupt source?")

    # Hand off to Docling Markdown extraction.
    return _load_docling(docx_bytes, original_name, ".docx")
# ---------------------------------------------------------------------
#  Per-format loaders
# ---------------------------------------------------------------------

def _load_text(file_bytes: bytes, original_name: str) -> list[Any]:
    from llama_index.core import Document

    text = file_bytes.decode("utf-8", errors="replace")
    if not text.strip():
        raise LoaderError("TXT file has no readable content.")
    return [Document(text=text, metadata={"source": original_name})]


@lru_cache(maxsize=1)
def _pdf_converter() -> Any:
    """Docling converter tuned for PDF: OCR on (scanned pages, Vietnamese) +
    accurate table-structure recovery for complex tables. Built once (heavy —
    loads TableFormer + OCR models), reused across uploads via the cache."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

    opts = PdfPipelineOptions()
    opts.do_ocr = True                       # OCR scanned / image-only pages
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE   # complex tables
    opts.table_structure_options.do_cell_matching = True

    # Vietnamese + English OCR. EasyOCR has solid `vi`; fall back to Docling's
    # default engine if EasyOCR isn't importable.
    try:
        from docling.datamodel.pipeline_options import EasyOcrOptions
        opts.ocr_options = EasyOcrOptions(lang=["vi", "en"])
    except Exception:  # noqa: BLE001
        logger.warning("EasyOCR unavailable — using Docling default OCR engine.")

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


@lru_cache(maxsize=1)
def _default_converter() -> Any:
    """Plain Docling converter for DOCX (no OCR needed). Built once."""
    from docling.document_converter import DocumentConverter
    return DocumentConverter()


def _load_docling(file_bytes: bytes, original_name: str, suffix: str) -> list[Any]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        converter = _pdf_converter() if suffix == ".pdf" else _default_converter()
        result = converter.convert(tmp_path)
        markdown_text = result.document.export_to_markdown()
        docling_doc = result.document
    except Exception as exc:
        raise LoaderError(f"Docling parse failed for {suffix}: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not markdown_text.strip():
        raise LoaderError(f"{suffix.upper().lstrip('.')} has no readable Markdown content.")

    # Quality gate — reject blurry / failed-OCR PDFs before they become useless chunks.
    if suffix == ".pdf":
        ok, reason = _assess_pdf_quality(markdown_text, docling_doc)
        if not ok:
            raise LoaderError(reason)

    return [
        DoclingResult(
            text=markdown_text,
            docling_doc=docling_doc,
            metadata={"source": original_name, "parser": "docling"},
        )
    ]


# ---------------------------------------------------------------------
#  PDF quality gate — blurry / failed-OCR detection
# ---------------------------------------------------------------------

_MIN_CHARS_PER_PAGE    = 50      # below = near-empty page (blur / blank scan)
_MIN_WORDS_PER_PAGE    = 12      # below = no real extractable text
_MIN_ALPHA_RATIO       = 0.30    # below = symbol soup / OCR noise
_MAX_REPLACEMENT_RATIO = 0.03    # decode / OCR garbage (U+FFFD)


def _assess_pdf_quality(text: str, docling_doc: Any) -> tuple[bool, str]:
    """Heuristic gate for blurry / low-quality / failed-OCR PDFs.

    Returns ``(ok, reason)``; ``reason`` is a Vietnamese, user-facing message
    when rejected. Conservative — only trips on clearly bad extractions, so
    dense valid documents (including table-heavy) pass.
    """
    body = text.strip()
    total = len(body)

    try:
        pages = max(1, len(getattr(docling_doc, "pages", {}) or {}))
    except Exception:  # noqa: BLE001
        pages = 1

    letters = sum(ch.isalpha() for ch in body)
    words = re.findall(r"[^\W\d_]{2,}", body)   # Unicode alpha tokens, len >= 2
    repl = body.count("�")

    chars_per_page = total / pages
    words_per_page = len(words) / pages
    alpha_ratio = letters / total if total else 0.0
    repl_ratio  = repl / total if total else 0.0

    near_empty = chars_per_page < _MIN_CHARS_PER_PAGE and words_per_page < _MIN_WORDS_PER_PAGE
    if total < 20 or near_empty:
        return False, (
            f"Chất lượng PDF quá thấp (có thể bị mờ hoặc scan kém). "
            f"Chỉ trích xuất được {total} ký tự / {pages} trang. "
            f"Vui lòng tải lên bản rõ nét hơn."
        )
    if repl_ratio > _MAX_REPLACEMENT_RATIO:
        return False, (
            f"PDF chứa quá nhiều ký tự lỗi ({repl_ratio:.0%}) — có thể do mã hóa "
            f"hoặc OCR thất bại. Vui lòng tải lên bản khác."
        )
    if alpha_ratio < _MIN_ALPHA_RATIO:
        return False, (
            f"Nội dung trích xuất chủ yếu là ký hiệu/nhiễu (chữ cái {alpha_ratio:.0%}) — "
            f"PDF có thể bị mờ hoặc OCR kém."
        )
    return True, ""
