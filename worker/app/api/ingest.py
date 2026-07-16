"""POST /ingest — multipart upload from the .NET gateway.

Pipeline:
    bytes → loader → chunker → embedder → vector store

The endpoint never raises uncaught exceptions: every failure mode lands
in a structured 422 response so the .NET side can update its document row
with a meaningful ``error_code`` / ``message``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import logging
import os
import threading
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..schemas.ingest import IngestResponse
from ..services.chunk_metadata import prepend_document_context_to_chunks
from ..services.chunker import Chunker
from ..services.embedder import Embedder
from ..services.loader import LoaderError, load_documents
from ..services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"], dependencies=[Depends(require_api_key)])

# Only one document processes at a time — Docling + bge-m3 + LibreOffice
# can each use 500MB+; concurrent ingests OOM a laptop.
_INGEST_LOCK = threading.Lock()

_MIN_RAM_GB = 1.0  # reject ingest if available RAM below this

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",                                                        # .doc (legacy)
    "text/plain",
    # Browsers sometimes can't sniff Office files and send these generic types —
    # the loader checks the filename extension separately, so it's safe to allow.
    "application/octet-stream",
    "",
}
    
# File extensions accepted as a fallback when the MIME type is generic or empty.
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}

# Defensive cap — the .NET side enforces 20 MB already, but be safe.
MAX_BYTES = 25 * 1024 * 1024


@dataclasses.dataclass
class _PipelineResult:
    """Result of ``_run_pipeline`` — chunk count plus the partial-ingest signal.

    ``partial=True`` when any source ``DoclingResult`` reported dropped pages
    (Fork 3 OCR OR-merge); ``partial_reason`` aggregates the human-readable
    reasons. Defaults keep the fully-successful path indistinguishable from
    the old bare-int return semantics.

    ``page_routes`` (Phase 0) carries the per-page ingestion manifest for PDFs:
    one entry per page with ``{page, density, chars, ocr_used, dropped}``. Empty
    for non-PDF paths. Surfaced to the response so an operator can see exactly
    which pages are holes instead of a single aggregated string.
    """

    chunk_count: int
    partial: bool = False
    partial_reason: str | None = None
    page_routes: list[dict] = dataclasses.field(default_factory=list)


@router.post(
    "/ingest",
    response_model=IngestResponse,
    responses={
        200: {"description": "Ingested successfully."},
        401: {"description": "Missing or invalid X-Worker-Api-Key."},
        422: {"description": "Validation / parse failure (body still uses IngestResponse shape)."},
    },
)
async def ingest_document(
    request: Request,
    file: Annotated[UploadFile, File(description="The document binary.")],
    document_id:   Annotated[str, Form()],
    department_id: Annotated[str, Form()],
    original_name: Annotated[str, Form()],
    mime_type:     Annotated[str, Form()],
) -> JSONResponse:
    started_at = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    # ---- 1. Validate document_id is a UUID ----
    try:
        uuid.UUID(document_id)
    except ValueError:
        return _failed(document_id, "INVALID_DOCUMENT_ID",
                       "document_id is not a valid UUID.", elapsed_ms())

    # ---- 2. Validate MIME type (with filename-extension fallback) ----
    # Some browsers can't sniff Office files and send generic / empty MIME
    # types. The real format check happens inside the loader anyway, so we
    # accept anything whose filename extension is on the allowlist.
    from pathlib import Path as _Path
    ext = _Path(original_name).suffix.lower()
    if mime_type not in ALLOWED_MIME_TYPES and ext not in ALLOWED_EXTENSIONS:
        return _failed(document_id, "UNSUPPORTED_MIME_TYPE",
                       f"MIME type '{mime_type}' not allowed.", elapsed_ms())

    # ---- 3. Read body into memory (capped) ----
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        return _failed(document_id, "EMPTY_FILE",
                       "Uploaded file is empty.", elapsed_ms())
    if len(file_bytes) > MAX_BYTES:
        return _failed(document_id, "FILE_TOO_LARGE",
                       f"File exceeds {MAX_BYTES} byte limit.", elapsed_ms())

    # ---- 4. Pull singletons from app state ----
    chunker:      Chunker      = request.app.state.chunker
    embedder:     Embedder     = request.app.state.embedder
    vector_store: VectorStore  = request.app.state.vector_store

    # ---- 5. Pipeline (heavy: run OFF the event loop so a slow ingest never
    #      blocks concurrent /api/query — critical on the throttled 1-thread host) ----
    try:
        pipeline_result = await asyncio.to_thread(
            _run_pipeline,
            file_bytes, mime_type, original_name,
            document_id, department_id,
            chunker, embedder, vector_store,
        )

        logger.info(
            "ingest_ok document_id=%s dept=%s chunks=%d partial=%s "
            "pages=%d scanned=%d dropped=%d elapsed_ms=%d",
            document_id, department_id, pipeline_result.chunk_count,
            pipeline_result.partial,
            len(pipeline_result.page_routes),
            sum(1 for r in pipeline_result.page_routes if r.get("ocr_used")),
            sum(1 for r in pipeline_result.page_routes if r.get("dropped")),
            elapsed_ms(),
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=IngestResponse(
                document_id=document_id,
                status="success",
                chunk_count=pipeline_result.chunk_count,
                elapsed_ms=elapsed_ms(),
                partial=pipeline_result.partial,
                partial_reason=pipeline_result.partial_reason,
                page_routes=pipeline_result.page_routes,
            ).model_dump(mode="json"),
        )

    except LoaderError as exc:
        logger.warning("ingest_parse_error document_id=%s err=%s", document_id, exc)
        return _failed(document_id, "PARSE_ERROR", str(exc), elapsed_ms())

    except Exception as exc:  # noqa: BLE001 — last-line catch-all by design
        logger.exception("ingest_internal_error document_id=%s", document_id)
        return _failed(document_id, "INTERNAL_ERROR", str(exc), elapsed_ms())


# ---------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------

def _failed(document_id: str, error_code: str, message: str, elapsed: int) -> JSONResponse:
    """Uniform failure envelope — HTTP 422 + IngestResponse body."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=IngestResponse(
            document_id=document_id,
            status="failed",
            chunk_count=0,
            elapsed_ms=elapsed,
            error_code=error_code,
            message=message,
        ).model_dump(mode="json"),
    )


def _reap_leaked_children() -> None:
    """Reap leaked ``multiprocessing`` child processes after an ingest.

    Windows Docling/torch OCR work spawns ``multiprocessing`` child processes
    (EasyOCR / TableFormer worker forks) that never exit on this platform —
    they linger as idle multi-GB RSS corpses and starve system RAM until the
    next document's ``_check_ram`` precheck fails. Observed today: several idle
    children holding 1–6 GB RSS each. Tracked as debt item #22.

    Best-effort cleanup: only children whose cmdline carries the
    ``multiprocessing.spawn`` / ``spawn_main`` signature are touched — never a
    LibreOffice or any other legitimate child. Runs inside ``_INGEST_LOCK``
    after the pipeline completes, so no concurrent ingest owns a live child.
    The whole body is wrapped so it can never raise — reaping is cleanup and
    must never fail an ingest.
    """
    try:
        import psutil

        matched = []
        for child in psutil.Process().children(recursive=False):
            try:
                cmdline = " ".join(child.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:  # noqa: BLE001 — never let one child abort the sweep
                continue
            if "multiprocessing.spawn" not in cmdline and "spawn_main" not in cmdline:
                continue
            try:
                rss_mb = int(child.memory_info().rss / (1024 ** 2))
            except Exception:  # noqa: BLE001 — rss is best-effort logging only
                rss_mb = -1
            logger.warning("reaped_leaked_child pid=%d rss_mb=%d", child.pid, rss_mb)
            try:
                child.terminate()
                matched.append(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:  # noqa: BLE001
                continue
        if matched:
            _, alive = psutil.wait_procs(matched, timeout=5)
            for survivor in alive:
                try:
                    survivor.kill()
                except Exception:  # noqa: BLE001 — already terminating; ignore
                    continue
    except Exception:  # noqa: BLE001 — best-effort cleanup, must never fail an ingest
        logger.warning("child_reap_failed", exc_info=True)


def _check_ram() -> None:
    """Ensure enough RAM is free before starting a heavy ingest.

    When RAM is low, first reap any leaked ``multiprocessing`` children (a
    leaked OCR-worker corpse is the most likely cause — see
    ``_reap_leaked_children``), then wait up to 30s (re-checking every 3s) for
    RAM to recover. Only after the full wait without recovery does it raise
    ``LoaderError`` with the Vietnamese out-of-memory message.
    """
    try:
        import psutil
        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return  # can't check — proceed optimistically

    if avail_gb >= _MIN_RAM_GB:
        return

    # RAM is low — a leaked OCR-worker corpse is the most likely cause. Reap
    # first, then give the OS a chance to reclaim the freed pages.
    _reap_leaked_children()
    logger.warning(
        "ram_low_waiting avail_gb=%.1f need_gb=%.1f", avail_gb, _MIN_RAM_GB
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        time.sleep(3.0)
        try:
            avail_gb = psutil.virtual_memory().available / (1024 ** 3)
        except Exception:  # noqa: BLE001
            return  # can't re-check — proceed optimistically
        if avail_gb >= _MIN_RAM_GB:
            logger.info("ram_recovered avail_gb=%.1f", avail_gb)
            return

    raise LoaderError(
        f"Không đủ bộ nhớ để xử lý file (RAM trống: {avail_gb:.1f}GB, "
        f"cần ≥{_MIN_RAM_GB:.0f}GB). Đóng bớt ứng dụng và thử lại."
    )


def _run_pipeline(
    file_bytes: bytes,
    mime_type: str,
    original_name: str,
    document_id: str,
    department_id: str,
    chunker: Chunker,
    embedder: Embedder,
    vector_store: VectorStore,
) -> _PipelineResult:
    """Blocking ingest pipeline (load → chunk → embed → upsert).

    Serialized by ``_INGEST_LOCK`` — only one document at a time to prevent
    OOM on RAM-constrained machines.  Runs in a worker thread via
    ``asyncio.to_thread`` so it never blocks the FastAPI event loop.

    Returns a :class:`_PipelineResult` carrying the chunk count plus the
    aggregated partial-ingest signal (OR across all returned documents).
    """
    with _INGEST_LOCK:
        _check_ram()
        logger.info("ingest_pipeline_start doc=%s mime=%s", original_name, mime_type)
        try:
            documents = load_documents(file_bytes, mime_type, original_name)

            # Aggregate the partial signal across the returned documents. The
            # list is ``list[Any]`` — it may hold ``DoclingResult`` objects
            # (which carry ``.partial``) or plain ``Document`` objects (which
            # do not), so ``getattr`` with a default keeps this robust to any
            # future loader path that returns a different node type.
            partial = any(getattr(d, "partial", False) for d in documents)
            reasons = [
                r
                for r in (getattr(d, "partial_reason", None) for d in documents)
                if r
            ]
            partial_reason = "; ".join(reasons) if reasons else None

            # Phase 0: aggregate the per-page manifest across returned docs.
            page_routes: list[dict] = []
            for d in documents:
                page_routes.extend(getattr(d, "page_routes", []) or [])

            nodes = chunker.split(documents)
            if not nodes:
                raise LoaderError("NO_CONTENT: No chunks produced after splitting.")

            raw_texts = [n.get_content() for n in nodes]
            texts = prepend_document_context_to_chunks(raw_texts, original_name)
            vectors = embedder.encode(texts)

            chunk_count = vector_store.upsert_chunks(
                document_id=document_id,
                department_id=department_id,
                original_name=original_name,
                chunks=texts,
                vectors=vectors,
            )
            return _PipelineResult(
                chunk_count=chunk_count,
                partial=partial,
                partial_reason=partial_reason,
                page_routes=page_routes,
            )
        finally:
            # Release cached Docling PDF converters (EasyOCR + TableFormer) —
            # these are @lru_cache singletons that otherwise stay resident
            # forever, so a multi-file batch's RAM usage only ever climbs.
            # See release_pdf_converter_caches() docstring for the full story.
            from ..services.loader import release_pdf_converter_caches
            release_pdf_converter_caches()
            _reap_leaked_children()
            gc.collect()
