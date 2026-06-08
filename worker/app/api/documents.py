"""Document management endpoints (admin / batch).

Routes mounted under ``/api/documents``:
    - ``POST /upload``  — multi-file async ingest via arq (Redis queue)
    - ``DELETE /delete`` — single-call wipe by `document_id` or `file_name`

The synchronous, single-file ingest path lives at ``/api/ingest`` and is
the one the .NET background worker hits. These endpoints are operator
tools (multi-upload from the admin UI, cleanup after bad imports).

Auth: every route requires the shared ``X-Worker-Api-Key`` header.

Why arq + tmp files (not BackgroundTasks + raw bytes):
    1. BackgroundTasks run in the API process — embedding 50 docx files
       sequentially would block server restarts and consume RAM.
    2. Passing raw bytes through Redis would bloat the queue (a 20 MB
       docx becomes a 30 MB Redis value after JSON encoding).
    3. arq decouples API throughput from embedding throughput entirely.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from ..auth import require_api_key
from ..config import get_settings
from ..services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_api_key)],
)

# Constants
SUPPORTED_DOCX_EXTENSIONS: frozenset[str] = frozenset({".docx", ".doc"})
MAX_FILE_BYTES: int = 25 * 1024 * 1024                              # 25 MB / file
STREAM_CHUNK_BYTES: int = 1 * 1024 * 1024                           # 1 MB read chunks


# ====================================================================
#  Tmp directory (created once at import time)
# ====================================================================

def _resolve_tmp_dir() -> Path:
    d = Path(get_settings().tmp_uploads_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


TMP_DIR: Path = _resolve_tmp_dir()


# ====================================================================
#  POST /api/documents/upload — enqueue arq jobs
# ====================================================================

@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload multiple documents → enqueue background ingestion jobs",
)
async def upload_documents(
    request:       Request,
    files:         Annotated[list[UploadFile], File(description="One or more .docx files.")],
    department_id: Annotated[str,              Form(min_length=2, max_length=20,
                                                    description="Tenant code (e.g. IT, HR).")],
) -> dict:
    """Stream each upload to disk → enqueue one arq job per file → return 202.

    The response carries every assigned ``job_id`` so the caller can poll
    arq for status (or trust the eventual Qdrant search to reflect the
    new chunks). The Redis payload is **path-only** — never bytes.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files supplied.",
        )

    redis_pool = getattr(request.app.state, "arq_pool", None)
    if redis_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue unavailable (Redis pool not initialised).",
        )

    accepted: list[dict] = []
    skipped:  list[dict] = []

    for f in files:
        original_name = f.filename or "untitled.docx"
        ext = Path(original_name).suffix.lower()

        if ext not in SUPPORTED_DOCX_EXTENSIONS:
            skipped.append({"name": original_name, "reason": "unsupported_extension",
                            "extension": ext})
            continue

        # ---- 1) Stream to a unique tmp file (size-capped) ----
        document_id = str(uuid.uuid4())
        tmp_path    = TMP_DIR / f"{document_id}{ext}"

        size = await _save_upload_streamed(f, tmp_path, MAX_FILE_BYTES)
        if size is None:
            # over-limit or write failure (cleanup already done)
            skipped.append({"name": original_name, "reason": "too_large_or_io_error"})
            continue
        if size == 0:
            _safe_unlink(tmp_path)
            skipped.append({"name": original_name, "reason": "empty"})
            continue

        # ---- 2) Enqueue arq job ----
        try:
            job = await redis_pool.enqueue_job(
                "ingest_document_task",
                file_path     = str(tmp_path),
                original_name = original_name,
                department_id = department_id,
                document_id   = document_id,
            )
        except Exception as exc:                                       # noqa: BLE001
            logger.exception("enqueue_failed file=%s", original_name)
            _safe_unlink(tmp_path)
            skipped.append({"name": original_name, "reason": "enqueue_failed",
                            "message": str(exc)})
            continue

        if job is None:
            # arq returns None when a deduplicated job_id is already running.
            _safe_unlink(tmp_path)
            skipped.append({"name": original_name, "reason": "duplicate_job"})
            continue

        accepted.append({
            "document_id":   document_id,
            "original_name": original_name,
            "job_id":        job.job_id,
            "size_bytes":    size,
        })

    if not accepted and skipped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "No valid files queued.", "skipped": skipped},
        )

    return {
        "status":        "queued",
        "scheduled":     len(accepted),
        "skipped":       skipped,
        "department_id": department_id,
        "jobs":          accepted,
    }


# ====================================================================
#  DELETE /api/documents/delete — single-call filtered wipe
# ====================================================================

@router.delete(
    "/delete",
    status_code=status.HTTP_200_OK,
    summary="Delete every chunk belonging to a document or file name",
)
async def delete_document(
    request:     Request,
    document_id: Annotated[str | None, Query(min_length=8, max_length=64,
                                             description="Payload `document_id` (UUID).")] = None,
    file_name:   Annotated[str | None, Query(min_length=1, max_length=512,
                                             description="Payload `original_name`.")]    = None,
) -> dict:
    """One Qdrant `FilterSelector` call — no client-side id enumeration."""
    if not document_id and not file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either `document_id` or `file_name`.",
        )
    if document_id and file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide only one of `document_id` or `file_name`, not both.",
        )

    field, value = (
        ("document_id",   document_id) if document_id
        else ("original_name", file_name)
    )

    vector_store: VectorStore = request.app.state.vector_store

    try:
        import asyncio
        qdrant_status = await asyncio.to_thread(
            vector_store.delete_by_payload, field, value,
        )
    except Exception as exc:                                           # noqa: BLE001
        logger.exception("delete_failed field=%s value=%s", field, value)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Qdrant delete failed: {exc}",
        ) from exc

    logger.info("delete_ok field=%s value=%s qdrant_status=%s", field, value, qdrant_status)

    return {
        "status":        "deleted",
        "filter_field":  field,
        "filter_value":  value,
        "qdrant_status": qdrant_status,
    }


# ====================================================================
#  Internal helpers
# ====================================================================

async def _save_upload_streamed(
    upload:    UploadFile,
    target:    Path,
    max_bytes: int,
) -> int | None:
    """Stream `upload` to `target`. Aborts + cleans up if size > max_bytes.

    Returns the written byte count on success, ``None`` on size overflow
    or I/O failure.
    """
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    out.close()
                    _safe_unlink(target)
                    return None
                out.write(chunk)
    except OSError as exc:
        logger.warning("upload_disk_write_failed file=%s err=%s", upload.filename, exc)
        _safe_unlink(target)
        return None

    return size


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:                                                  # noqa: BLE001
        logger.warning("tmp_unlink_failed path=%s", path)
