"""POST /ingest — multipart upload from the .NET gateway.

Pipeline:
    bytes → loader → chunker → embedder → vector store

The endpoint never raises uncaught exceptions: every failure mode lands
in a structured 422 response so the .NET side can update its document row
with a meaningful ``error_code`` / ``message``.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..schemas.ingest import IngestResponse
from ..services.chunker import Chunker
from ..services.embedder import Embedder
from ..services.loader import LoaderError, load_documents
from ..services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"], dependencies=[Depends(require_api_key)])

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
    
print(f"DEBUG: File path is {os.path.abspath(__file__)}")
print(f"DEBUG: ALLOWED_MIME_TYPES = {ALLOWED_MIME_TYPES}")
# File extensions accepted as a fallback when the MIME type is generic or empty.
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}

# Defensive cap — the .NET side enforces 20 MB already, but be safe.
MAX_BYTES = 25 * 1024 * 1024


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

    # ---- 5. Pipeline ----
    try:
        documents = load_documents(file_bytes, mime_type, original_name)
        nodes = chunker.split(documents)
        if not nodes:
            return _failed(document_id, "NO_CONTENT",
                           "No chunks produced after splitting.", elapsed_ms())

        texts = [n.get_content() for n in nodes]
        vectors = embedder.encode(texts)

        chunk_count = vector_store.upsert_chunks(
            document_id=document_id,
            department_id=department_id,
            original_name=original_name,
            chunks=texts,
            vectors=vectors,
        )

        logger.info(
            "ingest_ok document_id=%s dept=%s chunks=%d elapsed_ms=%d",
            document_id, department_id, chunk_count, elapsed_ms(),
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=IngestResponse(
                document_id=document_id,
                status="success",
                chunk_count=chunk_count,
                elapsed_ms=elapsed_ms(),
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
