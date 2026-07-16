"""HyDE (Hypothetical Document Embeddings) for Vietnamese RAG queries.

Generates a hypothetical answer via a lightweight LLM, then embeds THAT
as the dense query vector. This bridges the semantic gap between short
user queries (e.g. "quyết định 1202") and dense enterprise/legal
documents where the raw query embedding would miss relevant chunks.

Gating:
  - Default OFF (``hyde_enabled=False`` in Settings).
  - Only active for short queries (below ``hyde_max_query_chars``).
  - On any failure, falls back to the raw query vector instantly.
  - Latency-bounded by the existing LLM timeout.

Design choice: HyDE replaces the dense query vector ONLY. The BM25
branch still uses the original (segmented) query text — keyword match
on the user's actual words is correct and should not be diluted by
a generated hypothetical.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def generate_hypothetical_answer(
    query: str,
    llm,
    *,
    max_query_chars: int = 80,
) -> str | None:
    """Generate a short hypothetical Vietnamese answer for HyDE embedding.

    Args:
        query: The user's original query.
        llm: The LlmRouter instance (Ollama).
        max_query_chars: Only generate HyDE for queries shorter than this.
            Long queries already carry enough semantic signal.

    Returns:
        A 2–3 sentence hypothetical answer, or None if HyDE should be
        skipped (query too long, LLM failure, or HyDE disabled by caller).

    The caller is responsible for deciding whether to use the result.
    The ``query.py`` endpoint gates on ``Settings.hyde_enabled`` before
    calling this function.
    """
    if not query or len(query) > max_query_chars:
        return None

    prompt = (
        "Bạn là trợ lý AI chuyên về tài liệu hành chính và pháp luật Việt Nam. "
        "Dưới đây là một câu hỏi ngắn. Hãy tạo một câu trả lời giả định ngắn gọn "
        "(2-3 câu) dựa trên kiến thức chung, như thể bạn đã đọc tài liệu liên quan. "
        "Chỉ trả lời, không giải thích thêm.\n\n"
        f"Câu hỏi: {query}\n\n"
        "Câu trả lời giả định:"
    )

    try:
        messages = [
            {"role": "user", "content": prompt},
        ]
        chunks: list[str] = []
        async for delta in llm.stream_chat(messages):
            chunks.append(delta)
        hypothetical = "".join(chunks).strip()
        if hypothetical and len(hypothetical) > 20:
            logger.info(
                "hyde_generated query_len=%d hypo_len=%d",
                len(query), len(hypothetical),
            )
            return hypothetical
        return None
    except Exception:  # noqa: BLE001 — HyDE must never block retrieval
        logger.warning("hyde_failed query_len=%d — using raw query", len(query))
        return None
