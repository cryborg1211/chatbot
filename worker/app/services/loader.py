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

    The chunker splits ``text`` (raw Markdown) at heading boundaries,
    preserving table formatting.  ``docling_doc`` is retained for
    potential future use.  ``metadata`` is forwarded into every ``TextNode``.

    ``partial`` / ``partial_reason`` carry the Fork 3 OCR-OR-merge signal:
    ``partial=True`` when some page batches were dropped during parsing, with
    a human-readable (Vietnamese) ``partial_reason`` describing which page
    ranges are missing. Defaults keep every non-PDF path backward-compatible.
    """

    text: str
    docling_doc: Any
    metadata: dict[str, Any]
    partial: bool = False
    partial_reason: str | None = None


class LoaderError(Exception):
    """Raised when the upload can't be parsed into usable text.

    Caller maps this to HTTP 422 with ``error_code = "PARSE_ERROR"``.
    """


_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".txt":  "text/plain",
}


def load_documents(file_bytes: bytes, mime_type: str, original_name: str) -> list[Any]:
    """Returns a list of :class:`llama_index.core.Document` objects.

    Empty files / no-text PDFs raise :class:`LoaderError`.
    """
    if not file_bytes:
        raise LoaderError("Uploaded file is empty.")

    # Normalise generic / empty MIME types using file extension.
    if not mime_type or mime_type in ("application/octet-stream", ""):
        ext = Path(original_name).suffix.lower()
        mime_type = _EXT_TO_MIME.get(ext, mime_type)

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


def _get_available_ram_gb() -> float:
    """Best-effort available RAM in GB. Returns inf if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return float("inf")


_OCR_RAM_THRESHOLD_GB = 4.0  # need at least 4GB free to safely run OCR
_TABLE_RAM_THRESHOLD_GB = 1.5  # need at least 1.5GB free for table structure models


@lru_cache(maxsize=1)
def _lightweight_pdf_converter() -> Any:
    """Lightweight PDF converter: no OCR, FAST table mode. Safe on laptops
    (~500MB RAM). Handles most Vietnamese government PDFs since they're
    digital (not scanned)."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.FAST
    opts.table_structure_options.do_cell_matching = True

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


@lru_cache(maxsize=1)
def _bare_pdf_converter() -> Any:
    """Bare minimum PDF converter: no OCR, no table structure. Uses ~200MB.
    Last resort when RAM is critically low."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = False

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


@lru_cache(maxsize=1)
def _ocr_pdf_converter() -> Any:
    """Heavy PDF converter: EasyOCR (Vietnamese) + FAST table mode.
    Only used when lightweight pass yields near-empty text (scanned PDF).
    Still uses FAST tables to keep RAM under control."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.FAST
    opts.table_structure_options.do_cell_matching = True

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


def _is_near_empty(markdown_text: str, docling_doc: Any) -> bool:
    """True when digital extraction yielded too little text — likely scanned."""
    body = markdown_text.strip()
    if len(body) < 100:
        return True
    try:
        pages = max(1, len(getattr(docling_doc, "pages", {}) or {}))
    except Exception:  # noqa: BLE001
        pages = 1
    words = len(re.findall(r"[^\W\d_]{2,}", body))
    return words / pages < _MIN_WORDS_PER_PAGE


_PDF_PAGE_BATCH_SIZE = 10   # pages per Docling convert() call
_OCR_PAGE_BATCH_SIZE = 4  # smaller than the digital-pass batch -- OCR (EasyOCR detection+recognition nets) is far more RAM-hungry per page than digital extraction or FAST TableFormer, so OCR batches use a tighter window to cap peak memory.


def _get_pdf_page_count(file_path: Path) -> int:
    """Quick page count without full parse. Falls back to 0 if unknown."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(file_path))
        count = len(pdf)
        pdf.close()
        return count
    except Exception:  # noqa: BLE001
        return 0


def _convert_pdf_batched(
    tmp_path: Path,
    converter: Any,
    total_pages: int,
    original_name: str,
    batch_size: int = _PDF_PAGE_BATCH_SIZE,
) -> tuple[str, Any, int, list[tuple[int, int]]]:
    """Convert a large PDF in page-range batches to avoid bad_alloc OOM.

    Returns ``(merged_markdown, last_docling_doc, failed_batch_count,
    failed_page_ranges)``. ``failed_page_ranges`` lists the ``(start, end)``
    page ranges of every batch that was skipped (OOM or parse error) so the
    caller can surface a partial-ingest signal instead of silently dropping
    pages. For small PDFs (<=batch size), this is a single pass — no batching,
    so no failure tracking is possible (it either fully succeeds or raises).
    """
    if total_pages <= batch_size:
        result = converter.convert(
            tmp_path,
            raises_on_error=False,
        )
        return result.document.export_to_markdown(), result.document, 0, []

    md_parts: list[str] = []
    last_doc = None
    failed_batches = 0
    failed_ranges: list[tuple[int, int]] = []

    for start in range(1, total_pages + 1, batch_size):
        end = min(start + batch_size - 1, total_pages)
        logger.info(
            "pdf_batch file=%s pages=%d-%d/%d ram=%.1fGB",
            original_name, start, end, total_pages, _get_available_ram_gb(),
        )

        try:
            result = converter.convert(
                tmp_path,
                page_range=(start, end),
                raises_on_error=False,
                )
            batch_md = result.document.export_to_markdown()
            if batch_md.strip():
                md_parts.append(batch_md)
            last_doc = result.document
        except Exception as exc:  # noqa: BLE001
            err_str = str(exc).lower()
            if "bad_alloc" in err_str or "memory" in err_str:
                logger.warning(
                    "pdf_batch_oom file=%s pages=%d-%d — skipping batch",
                    original_name, start, end,
                )
                failed_batches += 1
                failed_ranges.append((start, end))
            else:
                logger.warning(
                    "pdf_batch_error file=%s pages=%d-%d err=%s — skipping",
                    original_name, start, end, exc,
                )
                failed_batches += 1
                failed_ranges.append((start, end))

    merged = "\n\n".join(md_parts)
    if failed_batches > 0:
        logger.warning(
            "pdf_partial_extract file=%s failed_batches=%d/%d",
            original_name,
            failed_batches,
            (total_pages + batch_size - 1) // batch_size,
        )
    return merged, last_doc, failed_batches, failed_ranges


def _load_docling(file_bytes: bytes, original_name: str, suffix: str) -> list[Any]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    # Partial-ingest signal (Fork 3): set when any page batch is dropped.
    # For non-PDF paths these stay at their defaults (no page-batching mechanism).
    partial = False
    partial_reason: str | None = None

    try:
        if suffix == ".pdf":
            total_pages = _get_pdf_page_count(tmp_path)
            ram = _get_available_ram_gb()
            logger.info(
                "pdf_load file=%s pages=%d ram=%.1fGB",
                original_name, total_pages, ram,
            )

            if ram < _TABLE_RAM_THRESHOLD_GB:
                logger.warning(
                    "low_ram_bare_converter file=%s ram=%.1fGB — "
                    "disabling table structure to avoid OOM",
                    original_name, ram,
                )
                converter = _bare_pdf_converter()
            else:
                converter = _lightweight_pdf_converter()

            markdown_text, docling_doc, _digital_failed, digital_failed_ranges = (
                _convert_pdf_batched(
                    tmp_path, converter, total_pages, original_name,
                )
            )

            # Initialised before the OCR branch so the OR-merge below always
            # has a defined value even when OCR retry never runs.
            ocr_failed_ranges: list[tuple[int, int]] = []

            # If near-empty, the PDF is likely scanned — try OCR fallback
            if _is_near_empty(markdown_text, docling_doc):
                ram = _get_available_ram_gb()
                if ram < _OCR_RAM_THRESHOLD_GB:
                    logger.warning(
                        "scanned_pdf_low_ram file=%s ram_gb=%.1f — "
                        "skipping OCR to avoid OOM crash",
                        original_name, ram,
                    )
                    raise LoaderError(
                        f"File PDF này là bản scan (hình ảnh), cần OCR để đọc nội dung. "
                        f"Hiện không đủ bộ nhớ (RAM: {ram:.1f}GB, cần ≥{_OCR_RAM_THRESHOLD_GB:.0f}GB). "
                        f"Hãy đóng bớt ứng dụng và thử lại, hoặc tải lên bản PDF có text layer."
                    )
                else:
                    logger.info(
                        "near_empty_digital_pass file=%s — retrying with OCR (ram=%.1fGB)",
                        original_name, ram,
                    )
                    ocr_converter = _ocr_pdf_converter()
                    markdown_text, docling_doc, _ocr_failed, ocr_failed_ranges = (
                        _convert_pdf_batched(
                            tmp_path, ocr_converter, total_pages, original_name,
                            batch_size=_OCR_PAGE_BATCH_SIZE,
                        )
                    )

            # OR-merge: any page range dropped in EITHER pass counts as partial.
            # The digital pass and the OCR-retry pass may drop different batches,
            # so union both instead of letting the OCR pass overwrite the signal.
            all_failed_ranges = digital_failed_ranges + ocr_failed_ranges
            partial = bool(all_failed_ranges)
            if partial:
                partial_reason = (
                    f"Thiếu {len(all_failed_ranges)} nhóm trang: "
                    + ", ".join(f"{s}-{e}" for s, e in all_failed_ranges)
                )
        else:
            converter = _default_converter()
            result = converter.convert(tmp_path)
            markdown_text = result.document.export_to_markdown()
            docling_doc = result.document
    except MemoryError as exc:
        raise LoaderError(
            "Không đủ bộ nhớ (RAM) để xử lý file. "
            "Hãy đóng bớt ứng dụng và thử lại."
        ) from exc
    except LoaderError:
        raise
    except Exception as exc:
        err_str = str(exc).lower()
        if "bad_alloc" in err_str or "memory" in err_str:
            raise LoaderError(
                "Không đủ bộ nhớ (RAM) để xử lý file. "
                "Hãy đóng bớt ứng dụng và thử lại."
            ) from exc
        raise LoaderError(f"Docling parse failed for {suffix}: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not markdown_text or not markdown_text.strip():
        raise LoaderError(f"{suffix.upper().lstrip('.')} has no readable Markdown content.")

    if suffix == ".pdf":
        page_count = total_pages if total_pages > 0 else None
        ok, reason = _assess_pdf_quality(markdown_text, docling_doc, page_count)
        if not ok:
            raise LoaderError(reason)

    return [
        DoclingResult(
            text=markdown_text,
            docling_doc=docling_doc,
            metadata={"source": original_name, "parser": "docling"},
            partial=partial,
            partial_reason=partial_reason,
        )
    ]


# ---------------------------------------------------------------------
#  PDF quality gate — blurry / failed-OCR detection
# ---------------------------------------------------------------------

_MIN_CHARS_PER_PAGE    = 50      # below = near-empty page (blur / blank scan)
_MIN_WORDS_PER_PAGE    = 12      # below = no real extractable text
_MIN_ALPHA_RATIO       = 0.30    # below = symbol soup / OCR noise
_MAX_REPLACEMENT_RATIO = 0.03    # decode / OCR garbage (U+FFFD)


def _assess_pdf_quality(
    text: str, docling_doc: Any, known_page_count: int | None = None,
) -> tuple[bool, str]:
    """Heuristic gate for blurry / low-quality / failed-OCR PDFs.

    Returns ``(ok, reason)``; ``reason`` is a Vietnamese, user-facing message
    when rejected. Conservative — only trips on clearly bad extractions, so
    dense valid documents (including table-heavy) pass.
    """
    body = text.strip()
    total = len(body)

    if known_page_count and known_page_count > 0:
        pages = known_page_count
    else:
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
