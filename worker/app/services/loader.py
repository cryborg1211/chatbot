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

    ``page_routes`` carries the Phase 0 per-page ingestion manifest: one entry
    per PDF page with ``{page, density, chars, ocr_used, dropped}``. Empty for
    non-PDF paths.
    """

    text: str
    docling_doc: Any
    metadata: dict[str, Any]
    partial: bool = False
    partial_reason: str | None = None
    page_routes: list[dict[str, Any]] = dataclasses.field(default_factory=list)


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
# (reverted 07-2026 — was temporarily lowered to 2.0 for a single-doc test;
# restored because the real problem turned out to be multi-file RAM
# accumulation across a batch, which a lower per-doc threshold makes worse,
# not better. See release_pdf_converter_caches() for the actual fix.)
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


def release_pdf_converter_caches() -> None:
    """Drop the cached Docling converters (EasyOCR + TableFormer models).

    The four converter builders above are ``@lru_cache(maxsize=1)`` — once
    built, they stay resident in this process's memory forever, reused
    across every subsequent document. That's a real problem for MULTI-FILE
    uploads: RAM never returns to baseline between documents (only leaked
    ``multiprocessing`` child processes get reaped — see
    ``ingest._reap_leaked_children``, a different mechanism that does NOT
    touch these in-process cached objects). Sequential large PDFs can each
    push RAM a little higher with nothing giving it back, until a later
    file in the same batch OOMs even though it would have been fine on its
    own.

    Call this after each document finishes (see ``ingest._run_pipeline``'s
    ``finally`` block). Cost: the next PDF that needs OCR pays the EasyOCR/
    TableFormer model init cost again (a few seconds) instead of reusing a
    warm converter — a worthwhile trade for not OOM-ing partway through a
    multi-file batch.
    """
    for fn in (
        _lightweight_pdf_converter,
        _bare_pdf_converter,
        _ocr_pdf_converter,
        _default_converter,
    ):
        fn.cache_clear()


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


# ---------------------------------------------------------------------
#  Phase 0 — preemptive per-page text-density probe.
#  Density is classified from the PDF's NATIVE TEXT LAYER (pypdfium2),
#  before any Docling parse. This is what routes pages to the right
#  parser instead of the old reactive whole-doc OCR fallback.
#
#  Closes the "silent data loss on hybrid docs" gap: a 50-page govt PDF
#  with 5 scanned annex pages used to pass the aggregate near-empty
#  check (loader._is_near_empty measures words/page over the WHOLE doc),
#  so those 5 pages were never OCR'd.
# ---------------------------------------------------------------------

# Per-page text-layer density thresholds. Tuned to match the whole-doc
# heuristics in _assess_pdf_quality, but applied per page.
_DENSITY_MIN_TEXT_CHARS = 30      # a page with <30 text-layer chars is SCAN
_DENSITY_MIN_ALPHA_RATIO = 0.35   # symbol-soup / OCR-noise pages are SCAN


def _classify_page_density(text: str) -> str:
    """Classify ONE page's native text layer as ``text``/``scan``.

    ``hybrid`` is a document-level concept (mix of text and scan pages),
    not a per-page label, so this only returns the two atomic classes.

    Args:
        text: the page's native (digital) text layer, from pypdfium2.
              May be empty for a scanned/image-only page.

    Returns:
        ``"text"`` if the page has a usable digital text layer;
        ``"scan"`` if the page is image-only or near-empty (needs OCR).
    """
    if not text:
        return "scan"
    body = text.strip()
    chars = len(body)
    if chars < _DENSITY_MIN_TEXT_CHARS:
        return "scan"
    # Symbol soup / OCR-noise guard: a page full of glyphs but no letters
    # (e.g. a scanned page with a corrupt text layer) is still a scan.
    letters = sum(ch.isalpha() for ch in body)
    alpha_ratio = letters / chars if chars else 0.0
    if alpha_ratio < _DENSITY_MIN_ALPHA_RATIO:
        return "scan"
    return "text"


def _probe_page_density(file_path: Path, total_pages: int) -> list[str]:
    """Probe the native text layer of every page and classify density.

    Returns a list of length ``total_pages`` where index ``i`` holds the
    density label (``"text"``/``"scan"``) for page ``i+1``. On any failure
    to probe a page, that page defaults to ``"text"`` (optimistic) so we
    don't force OCR on a page we simply couldn't inspect — the secondary
    ``_is_near_empty`` whole-doc check remains as a safety net.

    Uses pypdfium2 (already a dependency for page counting) to read the
    text layer cheaply, WITHOUT running the heavy Docling/TableFormer OCR
    pipeline — this is a routing decision, not a parse.
    """
    if total_pages <= 0:
        return []
    densities: list[str] = []
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(file_path))
        try:
            for page_idx in range(total_pages):
                try:
                    page = pdf[page_idx]
                    textpage = page.get_textpage()
                    raw = textpage.get_text_range() or ""
                    textpage.close()
                    page.close()
                    densities.append(_classify_page_density(raw))
                except Exception:  # noqa: BLE001 — per-page failure, default optimistic
                    densities.append("text")
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001 — whole-probe failure: assume all-text, let near-empty catch it
        logger.warning("density_probe_failed file=%s — assuming all-text", file_path.name)
        return ["text"] * total_pages
    return densities


def _group_density_runs(densities: list[str]) -> list[tuple[int, int, str]]:
    """Compress a per-page density list into contiguous same-class runs.

    ``["text","text","scan","scan","text"]`` →
    ``[(1,2,"text"), (3,4,"scan"), (5,5,"text")]``.

    Returns runs in page order so the merged markdown preserves document
    sequence regardless of which converter parses each run.
    """
    if not densities:
        return []
    runs: list[tuple[int, int, str]] = []
    run_start = 1
    run_class = densities[0]
    for i in range(1, len(densities)):
        if densities[i] != run_class:
            runs.append((run_start, i, run_class))
            run_start = i + 1
            run_class = densities[i]
    runs.append((run_start, len(densities), run_class))
    return runs


def _mark_route(
    page_routes: list[dict[str, Any]],
    page: int,
    *,
    ocr_used: bool | None = None,
    dropped: bool | None = None,
    chars: int | None = None,
) -> None:
    """Update ONE page manifest entry. Page numbers are 1-based.

    Mutates in place; out-of-range pages are ignored defensively.
    """
    idx = page - 1
    if 0 <= idx < len(page_routes):
        if ocr_used is not None:
            page_routes[idx]["ocr_used"] = ocr_used
        if dropped is not None:
            page_routes[idx]["dropped"] = dropped
        if chars is not None:
            # Treat any non-zero signal as "produced text"; exact count is
            # distributed separately by _distribute_chars.
            if chars == 0:
                page_routes[idx]["chars"] = 0


def _distribute_chars(page_routes: list[dict[str, Any]], markdown_text: str) -> None:
    """Best-effort per-page char attribution.

    Exact per-page attribution would require per-page convert output (Docling
    merges batches into one markdown string). This evenly splits the merged
    text across pages that actually produced content, so the manifest carries
    a non-zero ``chars`` signal for parsed pages and 0 for dropped ones.
    It's an audit signal, not a precision metric.
    """
    if not page_routes or not markdown_text:
        return
    produced = [r for r in page_routes if not r["dropped"]]
    if not produced:
        return
    share = max(0, len(markdown_text) // len(produced))
    for r in produced:
        r["chars"] = share


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


def _convert_pdf_page_ranges(
    tmp_path: Path,
    converter: Any,
    page_ranges: list[tuple[int, int]],
    original_name: str,
    batch_size: int = _OCR_PAGE_BATCH_SIZE,
) -> tuple[str, Any, list[tuple[int, int]]]:
    """Convert ONLY a specific set of page ranges (Phase 0 OCR routing).

    Used to OCR just the scan-classified pages of a hybrid PDF instead of
    re-running the heavy OCR converter over the whole document. Mirrors
    ``_convert_pdf_batched``'s error handling but operates on an explicit
    list of ``(start, end)`` ranges rather than a full sequential sweep.

    Returns ``(merged_markdown, last_docling_doc, failed_ranges)``.
    """
    md_parts: list[str] = []
    last_doc: Any = None
    failed_ranges: list[tuple[int, int]] = []

    for (start, end) in page_ranges:
        logger.info(
            "pdf_range_convert file=%s pages=%d-%d (targeted) ram=%.1fGB",
            original_name, start, end, _get_available_ram_gb(),
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
            logger.warning(
                "pdf_range_error file=%s pages=%d-%d err=%s — skipping",
                original_name, start, end, exc,
            )
            failed_ranges.append((start, end))

    return "\n\n".join(md_parts), last_doc, failed_ranges


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

            # ---- Phase 0: preemptive per-page density probe ----
            # Route pages to the right parser BEFORE parsing. A hybrid doc
            # (text pages + scanned annex pages) is now detected at page
            # granularity; only scan pages get the expensive OCR pass.
            densities = _probe_page_density(tmp_path, total_pages)
            scan_pages = [i + 1 for i, d in enumerate(densities) if d == "scan"]
            text_pages = [i + 1 for i, d in enumerate(densities) if d == "text"]
            logger.info(
                "density_probe file=%s text_pages=%d scan_pages=%d%s",
                original_name, len(text_pages), len(scan_pages),
                f" scan={scan_pages[:20]}" if scan_pages else "",
            )

            if ram < _TABLE_RAM_THRESHOLD_GB:
                logger.warning(
                    "low_ram_bare_converter file=%s ram=%.1fGB — "
                    "disabling table structure to avoid OOM",
                    original_name, ram,
                )
                digital_converter = _bare_pdf_converter()
            else:
                digital_converter = _lightweight_pdf_converter()

            # One manifest entry per page, filled in as each range is parsed.
            page_routes: list[dict[str, Any]] = [
                {
                    "page": p,
                    "density": densities[p - 1],
                    "chars": 0,
                    "ocr_used": False,
                    "dropped": False,
                }
                for p in range(1, total_pages + 1)
            ]

            # Ordered conversion: contiguous same-density runs are parsed
            # together, in page order, so the merged markdown preserves
            # document order. Text runs use the digital converter; scan
            # runs use the OCR converter.
            ordered_runs = _group_density_runs(densities)
            md_parts: list[str] = []
            docling_doc: Any = None
            all_failed_ranges: list[tuple[int, int]] = []

            for run_start, run_end, run_density in ordered_runs:
                if run_density == "scan":
                    # OCR needs more RAM than digital parse. If we can't
                    # afford it, skip THIS range (not the whole doc) and
                    # mark its pages dropped — unless the entire PDF is a
                    # scan, in which case there's nothing to return.
                    ocr_ram = _get_available_ram_gb()
                    if ocr_ram < _OCR_RAM_THRESHOLD_GB:
                        if not text_pages:
                            raise LoaderError(
                                f"File PDF này là bản scan (hình ảnh), cần OCR để đọc nội dung. "
                                f"Hiện không đủ bộ nhớ (RAM: {ocr_ram:.1f}GB, "
                                f"cần ≥{_OCR_RAM_THRESHOLD_GB:.0f}GB). "
                                f"Hãy đóng bớt ứng dụng và thử lại, hoặc tải lên bản PDF có text layer."
                            )
                        logger.warning(
                            "scan_range_skipped_low_ram file=%s pages=%d-%d "
                            "ram=%.1fGB — marking pages dropped",
                            original_name, run_start, run_end, ocr_ram,
                        )
                        for p in range(run_start, run_end + 1):
                            _mark_route(page_routes, p, dropped=True)
                        all_failed_ranges.append((run_start, run_end))
                        continue

                    logger.info(
                        "ocr_range file=%s pages=%d-%d (scan) ram=%.1fGB",
                        original_name, run_start, run_end, ocr_ram,
                    )
                    ocr_converter = _ocr_pdf_converter()
                    run_md, docling_doc, run_failed = _convert_pdf_page_ranges(
                        tmp_path, ocr_converter,
                        [(run_start, run_end)], original_name,
                        batch_size=_OCR_PAGE_BATCH_SIZE,
                    )
                    for p in range(run_start, run_end + 1):
                        _mark_route(page_routes, p, ocr_used=True)
                else:
                    run_md, last_doc, run_failed = _convert_pdf_page_ranges(
                        tmp_path, digital_converter,
                        [(run_start, run_end)], original_name,
                        batch_size=_PDF_PAGE_BATCH_SIZE,
                    )
                    if last_doc is not None:
                        docling_doc = last_doc

                if run_md.strip():
                    md_parts.append(run_md)
                    for p in range(run_start, run_end + 1):
                        _mark_route(page_routes, p, chars=1)  # populated below
                for fr in run_failed:
                    all_failed_ranges.append(fr)
                    for p in range(fr[0], fr[1] + 1):
                        _mark_route(page_routes, p, dropped=True)

            markdown_text = "\n\n".join(md_parts)

            # Secondary safety net: if the density probe mis-classified a
            # corrupt-but-textual PDF (probe saw glyphs, Docling got nothing),
            # fall back to the original whole-doc OCR path. Preserves the
            # Fork 3 regression-test behaviour.
            if _is_near_empty(markdown_text, docling_doc) and not md_parts:
                logger.warning(
                    "density_probe_missed file=%s — whole-doc near-empty, "
                    "falling back to full OCR pass", original_name,
                )
                net_ram = _get_available_ram_gb()
                if net_ram < _OCR_RAM_THRESHOLD_GB:
                    raise LoaderError(
                        f"File PDF này là bản scan (hình ảnh), cần OCR để đọc nội dung. "
                        f"Hiện không đủ bộ nhớ (RAM: {net_ram:.1f}GB, "
                        f"cần ≥{_OCR_RAM_THRESHOLD_GB:.0f}GB). "
                        f"Hãy đóng bớt ứng dụng và thử lại, hoặc tải lên bản PDF có text layer."
                    )
                ocr_converter = _ocr_pdf_converter()
                markdown_text, docling_doc, _ocr_failed, ocr_failed_ranges = (
                    _convert_pdf_batched(
                        tmp_path, ocr_converter, total_pages, original_name,
                        batch_size=_OCR_PAGE_BATCH_SIZE,
                    )
                )
                all_failed_ranges.extend(ocr_failed_ranges)
                for p in range(1, total_pages + 1):
                    _mark_route(page_routes, p, ocr_used=True)

            # Populate approximate char counts per page from the merged text.
            # Exact per-page attribution requires per-page convert output;
            # this even split is a best-effort audit signal, not exact.
            _distribute_chars(page_routes, markdown_text)

            partial = bool(all_failed_ranges)
            if partial:
                partial_reason = (
                    f"Thiếu {len(all_failed_ranges)} nhóm trang: "
                    + ", ".join(f"{s}-{e}" for s, e in all_failed_ranges)
                )
            else:
                partial_reason = None
        else:
            converter = _default_converter()
            result = converter.convert(tmp_path)
            markdown_text = result.document.export_to_markdown()
            docling_doc = result.document
            page_routes = []
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
            page_routes=page_routes if suffix == ".pdf" else [],
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
