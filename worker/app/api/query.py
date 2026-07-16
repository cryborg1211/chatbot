"""POST /api/query — RAG chat over Server-Sent Events.

Stream order (locked, matches .NET `QueryEvent` discriminated union):
    1. `sources`  — retrieved chunks
    2. `token` …  — assistant reply deltas
    3. `done`     — terminal marker (always emitted on a clean stream)
    or:
    1. `sources`  — (optional, sometimes skipped on early failure)
    2. `error`    — out-of-band failure message
    3. `done`     — with finish_reason="error"

Phase 1 flow:
    1. (Optional HyDE) Generate hypothetical answer → embed as dense query vector
    2. Segment query for BM25 (pyvi Vietnamese word segmentation)
    3. Hybrid retrieve: dense cosine + BM25 sparse vector, RRF-fused server-side
    4. (Optional reranker) Cross-encoder rescore the RRF top-N
    5. Build messages + stream LLM tokens
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..auth import require_api_key
from ..schemas.query import QueryRequest
from ..services.segmenter import segment

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"], dependencies=[Depends(require_api_key)])


@router.post("/query")
async def query_endpoint(req: QueryRequest, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _stream_events(req, request.app.state),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            # Disable any intermediary buffering (e.g. nginx).
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------
#  Event stream generator
# ---------------------------------------------------------------------

async def _stream_events(req: QueryRequest, state) -> AsyncIterator[bytes]:
    started_at = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    # ---- 1. Embed query (off the event loop) ----
    # Phase 1: HyDE — optionally generate a hypothetical answer and embed THAT
    # as the dense query vector. Falls back to raw query embed on any failure.
    try:
        settings = state.settings
        embed_text = req.query

        if settings.hyde_enabled:
            from ..services.hyde import generate_hypothetical_answer
            hypothetical = await generate_hypothetical_answer(
                req.query,
                state.llm,
                max_query_chars=settings.hyde_max_query_chars,
            )
            if hypothetical:
                embed_text = hypothetical

        query_vec = (await asyncio.to_thread(state.embedder.encode, [embed_text]))[0]
    except Exception as exc:                                        # noqa: BLE001
        logger.exception("query_embed_failed dept=%s", req.department_id)
        yield _sse("error", {"message": f"Embedding failed: {exc}"})
        yield _sse("done",  {"finish_reason": "error", "latency_ms": elapsed_ms()})
        return

    # ---- 2. Segment query for BM25 (same segmenter as ingest) ----
    query_segmented = segment(req.query)

    # ---- 3. Hybrid retrieve (tenant-filtered) ----
    try:
        sources = await asyncio.to_thread(
            lambda: state.retriever.search(
                query_vector=query_vec,
                query_text_segmented=query_segmented,
                department_id=req.department_id,
                top_k=req.top_k or state.retrieval_top_k,
                prefetch_limit=settings.prefetch_top_n,
            )
        )
    except Exception as exc:                                        # noqa: BLE001
        logger.exception("query_retrieve_failed dept=%s", req.department_id)
        yield _sse("error", {"message": f"Retrieval failed: {exc}"})
        yield _sse("done",  {"finish_reason": "error", "latency_ms": elapsed_ms()})
        return

    # ---- 4. Reranker (optional, RAM-guarded) ----
    if settings.reranker_enabled and state.reranker is not None:
        try:
            sources = await asyncio.to_thread(
                lambda: _rerank_sources(
                    state.reranker,
                    req.query,
                    sources,
                    top_k=req.top_k or state.retrieval_top_k,
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("reranker_failed dept=%s — using RRF order",
                           req.department_id, exc_info=True)

    # ---- 5. Emit sources event ----
    # Exclude ``text`` (full chunk) from the wire — the .NET gateway/browser only needs the
    # truncated ``snippet`` for citations. This keeps the SSE payload byte-identical to before
    # the full-text field was added and avoids shipping ~2.6k-char chunks per hit.
    yield _sse("sources", {
        "documents": [s.model_dump(mode="json", exclude={"text"}) for s in sources],
    })

    # ---- 6. Build messages (system prompt + history + current question) ----
    system_prompt = state.prompt_builder.build_system(sources)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": req.query})

    # ---- 7. Stream LLM tokens (model/temperature may be overridden per request) ----
    try:
        llm = _select_llm(state, req)
    except Exception as exc:                                        # noqa: BLE001
        logger.exception("query_llm_select_failed dept=%s", req.department_id)
        yield _sse("error", {"message": f"LLM selection failed: {exc}"})
        yield _sse("done",  {"finish_reason": "error", "latency_ms": elapsed_ms()})
        return

    delta_count = 0
    try:
        async for delta in llm.stream_chat(messages):
            delta_count += 1
            yield _sse("token", {"content": delta})
    except Exception as exc:                                        # noqa: BLE001
        logger.exception("query_llm_failed dept=%s", req.department_id)
        yield _sse("error", {"message": f"LLM failed: {exc}"})
        yield _sse("done",  {"finish_reason": "error", "latency_ms": elapsed_ms()})
        return

    # ---- 8. Done ----
    logger.info(
        "query_ok dept=%s sources=%d deltas=%d elapsed_ms=%d hyde=%s reranked=%s",
        req.department_id, len(sources), delta_count, elapsed_ms(),
        settings.hyde_enabled,
        settings.reranker_enabled,
    )
    yield _sse("done", {
        "finish_reason":     "stop",
        "latency_ms":        elapsed_ms(),
        "prompt_tokens":     None,            # Ollama doesn't expose this via llama-index stream
        "completion_tokens": delta_count,     # approximation — one per stream chunk
    })


# ---------------------------------------------------------------------
#  Reranker integration
# ---------------------------------------------------------------------

def _rerank_sources(reranker, query: str, sources: list, *, top_k: int) -> list:
    """Rescore sources via cross-encoder and return re-ordered top-k.

    Preserves the ``RetrievedSource`` shape; only the order and score change.
    """
    from ..services.retriever import RetrievedSource

    texts = [s.text for s in sources]
    ranked = reranker.rerank(query, texts, top_k=top_k)

    return [
        RetrievedSource(
            id=sources[idx].id,
            document_id=sources[idx].document_id,
            title=sources[idx].title,
            text=sources[idx].text,
            snippet=sources[idx].snippet,
            score=score,
        )
        for idx, score in ranked
    ]


# ---------------------------------------------------------------------
#  LLM selection (per-request model / temperature override)
# ---------------------------------------------------------------------

def _select_llm(state, req: QueryRequest):
    """Pick the LLM for this request. Ollama (default) reuses the boot singleton
    unless model/temperature differ; a cloud provider always builds a fresh
    router with the per-request api key (no key is ever cached)."""
    from ..services.llm_router import LlmRouter

    cfg = state.ollama_cfg
    provider = (req.provider or "ollama").strip().lower()
    temperature = req.temperature if req.temperature is not None else cfg["temperature"]
    model = (req.model or "").strip() or state.default_model

    if provider == "ollama":
        if model == state.llm.model and temperature == cfg["temperature"]:
            return state.llm
        return LlmRouter(
            model=model,
            provider="ollama",
            base_url=cfg["base_url"],
            timeout=cfg["timeout"],
            temperature=temperature,
            num_ctx=cfg["num_ctx"],
        )

    return LlmRouter(
        model=model,
        provider=provider,
        api_key=req.api_key,
        timeout=cfg["timeout"],
        temperature=temperature,
    )


# ---------------------------------------------------------------------
#  SSE wire formatter
# ---------------------------------------------------------------------

def _sse(event: str, data: dict) -> bytes:
    """Encode one SSE event. UTF-8 bytes ready for the wire.

    Format:
        event: <name>\n
        data: <json>\n
        \n            ← required blank line dispatches the event
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
