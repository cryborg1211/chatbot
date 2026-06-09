"""FastAPI entry point.

Run with:
    uvicorn app.main:app --reload --port 8000

Boot sequence (in lifespan):
    1. Load Settings from env / .env
    2. Instantiate Embedder (downloads bge-m3 on first run → slow!)
    3. Connect to Qdrant + ensure_collection
    4. Stash singletons in app.state for route handlers to pull
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
import os
from typing import AsyncIterator

from fastapi import FastAPI
from qdrant_client import QdrantClient

from arq.connections import create_pool

from .api import documents, health, ingest, query
from .config import get_settings
from .queue_worker import build_redis_settings
from .services.chunker import Chunker
from .services.embedder import Embedder
from .services.llm_router import LlmRouter
from .services.prompt_builder import PromptBuilder
from .services.retriever import Retriever
from .services.vectorstore import VectorStore

logger = logging.getLogger(__name__)

TMP_UPLOADS_DIR = "tmp_uploads"
os.makedirs(TMP_UPLOADS_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    
    settings = get_settings()

    # ---- Logging ----
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    logger.info("worker_starting embed_model=%s qdrant=%s",
                settings.embed_model, settings.qdrant_url)

    # ---- Embedder (singleton, slow init) ----
    embedder = Embedder(settings.embed_model)

    # ---- Qdrant ----
    qdrant = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )
    vector_store = VectorStore(
        client=qdrant,
        collection=settings.collection_name,
        vector_size=settings.vector_size,
    )
    vector_store.ensure_collection()

    # ---- Phase 3: retriever, prompt builder, LLM ----
    retriever      = Retriever(client=qdrant, collection=settings.collection_name)
    prompt_builder = PromptBuilder()
    llm_router     = LlmRouter(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
        temperature=settings.ollama_temperature,
    )

    # ---- Stash on app.state ----
    app.state.embedder        = embedder
    app.state.vector_store    = vector_store
    app.state.chunker         = Chunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    app.state.retriever       = retriever
    app.state.prompt_builder  = prompt_builder
    app.state.llm             = llm_router
    app.state.retrieval_top_k = settings.retrieval_top_k

    # ---- arq Redis pool (used by /api/documents/upload) ----
    arq_pool = None
    try:
        arq_pool = await create_pool(build_redis_settings(settings))
        app.state.arq_pool = arq_pool
        logger.info("arq_pool_ready redis=%s:%s", settings.redis_host, settings.redis_port)
    except Exception:                                                  # noqa: BLE001
        # Worker still serves /health, /api/ingest, /api/query without the queue.
        app.state.arq_pool = None
        logger.exception("arq_pool_init_failed — /api/documents/upload disabled")

    logger.info("worker_ready")
    try:
        yield
    finally:
        logger.info("worker_shutting_down")
        if arq_pool is not None:
            try:
                await arq_pool.close()
            except Exception:                                          # noqa: BLE001
                logger.warning("arq_pool_close_failed")
        qdrant.close()


app = FastAPI(
    title="LD3 RAG Worker",
    version="0.1.0",
    description=(
        "AI worker for the LD3 RAG chatbot. Embeds documents into Qdrant and "
        "(in Phase 3) serves RAG query results. Called only by the .NET gateway."
    ),
    lifespan=lifespan,
)

# ---- Routes ----
# /health stays at root so container/load-balancer probes don't need to
# know the API prefix. Business routes are namespaced under /api to match
# the .NET gateway's `AiWorker:BaseUrl` (= "http://localhost:8000/api").
app.include_router(health.router)
app.include_router(ingest.router,    prefix="/api")
app.include_router(query.router,     prefix="/api")
app.include_router(documents.router, prefix="/api")    # /api/documents/upload + /api/documents/delete
