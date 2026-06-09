"""arq Redis worker — async document ingestion.

Run with:
    arq app.queue_worker.WorkerSettings

The API process **only enqueues** jobs. This worker process owns the
heavy lifting (DocxProcessor → bge-m3 embedding → Qdrant upsert).

Design rules:
- API never passes raw bytes through Redis. Files land on disk at
  ``$TMP_UPLOADS_DIR`` and the queue payload is path-only.
- The worker deletes the temp file on EVERY exit path (success, failure,
  parse error) so the directory cannot grow without bound.
- Heavy singletons (bge-m3 embedder, Qdrant client) load **once** in
  ``worker_startup`` and live on ``ctx``. They never reload per job.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from qdrant_client import QdrantClient

from .config import Settings, get_settings
from .services.chunk_metadata import prepend_document_context_to_chunks
from .services.embedder import Embedder
from .services.preprocessing import DocxProcessingError, DocxProcessor
from .services.vectorstore import VectorStore

logger = logging.getLogger(__name__)


# =====================================================================
#  Helpers
# =====================================================================

def build_redis_settings(settings: Settings | None = None) -> RedisSettings:
    """Construct an :class:`arq.connections.RedisSettings` from app settings.

    Importable from both the API (for ``create_pool``) and the worker.
    """
    s = settings or get_settings()
    return RedisSettings(
        host=s.redis_host,
        port=s.redis_port,
        database=s.redis_db,
        password=(s.redis_password or None),
    )


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:                                                  # noqa: BLE001
        logger.warning("tmp_unlink_failed path=%s", path)


# =====================================================================
#  Task function
# =====================================================================

async def ingest_document_task(
    ctx: dict[str, Any],
    file_path:     str,
    original_name: str,
    department_id: str,
    document_id:   str,
) -> dict[str, Any]:
    """Read DOCX → preprocess → embed → upsert → delete tmp file.

    Always returns a JSON-serialisable dict so arq can store it as the
    job result. Never raises out of the function — arq would otherwise
    retry the job, which is undesirable for parse / embed errors.
    """
    path         = Path(file_path)
    embedder:     Embedder     = ctx["embedder"]
    vector_store: VectorStore  = ctx["vector_store"]
    processor:    DocxProcessor = ctx["processor"]

    if not path.is_file():
        logger.error("ingest_missing_tmp_file path=%s", path)
        return {"status": "failed", "reason": "tmp_file_missing", "path": str(path)}

    try:
        try:
            # ---- 1) Parse + clean + chunk (off the event loop) ----
            chunks = await asyncio.to_thread(processor.process_file, path)
        except DocxProcessingError as exc:
            logger.error("ingest_preprocess_failed file=%s err=%s", original_name, exc)
            return {"status": "failed", "reason": "preprocess_error", "message": str(exc)}
        except Exception:                                              # noqa: BLE001
            logger.exception("ingest_preprocess_crashed file=%s", original_name)
            return {"status": "failed", "reason": "internal_error"}

        if not chunks:
            return {"status": "failed", "reason": "no_chunks"}

        raw_texts = [c.text for c in chunks]
        texts = prepend_document_context_to_chunks(raw_texts, original_name)

        # ---- 2) Embed (heavy — off the event loop) ----
        try:
            vectors = await asyncio.to_thread(embedder.encode, texts)
        except Exception:                                              # noqa: BLE001
            logger.exception("ingest_embed_failed file=%s", original_name)
            return {"status": "failed", "reason": "embed_error"}

        # ---- 3) Qdrant upsert (single round trip) ----
        try:
            await asyncio.to_thread(
                vector_store.upsert_chunks,
                document_id=document_id,
                department_id=department_id,
                original_name=original_name,
                chunks=texts,
                vectors=vectors,
            )
        except Exception:                                              # noqa: BLE001
            logger.exception("ingest_upsert_failed file=%s", original_name)
            return {"status": "failed", "reason": "upsert_error"}

        logger.info(
            "ingest_ok file=%s document_id=%s dept=%s chunks=%d",
            original_name, document_id, department_id, len(chunks),
        )
        return {
            "status":       "success",
            "document_id":  document_id,
            "chunk_count":  len(chunks),
        }
    finally:
        # Cleanup tmp file on EVERY exit path — success, failure, crash.
        _safe_unlink(path)


# =====================================================================
#  Worker lifecycle (loads heavy singletons once per worker process)
# =====================================================================

async def worker_startup(ctx: dict[str, Any]) -> None:
    s = get_settings()

    logging.basicConfig(
        level=s.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    logger.info(
        "arq_worker_starting embed_model=%s qdrant=%s collection=%s",
        s.embed_model, s.qdrant_url, s.collection_name,
    )

    embedder     = Embedder(s.embed_model)
    qdrant       = QdrantClient(url=s.qdrant_url, api_key=(s.qdrant_api_key or None))
    vector_store = VectorStore(
        client=qdrant,
        collection=s.collection_name,
        vector_size=s.vector_size,
    )
    vector_store.ensure_collection()

    ctx["embedder"]     = embedder
    ctx["qdrant"]       = qdrant
    ctx["vector_store"] = vector_store
    ctx["processor"]    = DocxProcessor(chunk_size=s.chunk_size, chunk_overlap=s.chunk_overlap)

    logger.info("arq_worker_ready")


async def worker_shutdown(ctx: dict[str, Any]) -> None:
    qdrant: QdrantClient | None = ctx.get("qdrant")
    if qdrant is not None:
        try:
            qdrant.close()
        except Exception:                                              # noqa: BLE001
            logger.warning("qdrant_close_failed")
    logger.info("arq_worker_shutting_down")


# =====================================================================
#  arq entry point — discovered by the `arq` CLI
# =====================================================================

class WorkerSettings:
    """`arq` discovers this class via the CLI argument:

        arq app.queue_worker.WorkerSettings
    """

    functions      = [ingest_document_task]
    on_startup     = worker_startup
    on_shutdown    = worker_shutdown
    redis_settings = build_redis_settings()

    # bge-m3 is CPU/GPU heavy — keep concurrency low on a single host.
    max_jobs   = 1
    # Per-job ceiling (seconds). 10 min covers a 25 MB docx on a slow CPU.
    job_timeout = 600
    # Keep the job result in Redis for 1 h so the API can poll status.
    keep_result = 3600
