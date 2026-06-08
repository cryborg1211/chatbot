"""LLM backend — v1 routes everything to Ollama (Qwen 2.5 / Gemma / DeepSeek).

Wrapped behind a thin class so swapping in Gemini / OpenAI later is a
single-file change (master plan §3.7, parking lot §7).

This module also OWNS the global anti-hallucination system prompt. The
strict "read tables row-by-row, never invent numbers" instructions are
forced at the front of every chat — callers cannot skip them by passing
their own system message; we merge instead of replace.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
#  Global, enforced system prompt — runs in front of the RAG context.
#  Keeping it as a module-level constant so it can be imported by tests
#  / monitoring dashboards without spinning up the router.
# ---------------------------------------------------------------------

ENFORCED_SYSTEM_PROMPT: str = """\
You are an intelligent data analysis expert. Below are the provided context documents, which may contain tables formatted in Markdown (separated by '|' characters).

When processing table data, you MUST strictly adhere to the following rules:
1. READ BY ROWS AND COLUMNS: Accurately map column headers to the corresponding data of each row. Do not mismatch data from different rows or columns.
2. EXTRACT FIRST, CALCULATE LATER: If the query requires summation, filtering, or comparison, explicitly list out the retrieved data rows first (e.g., Item A: X, Item B: Y) before performing calculations.
3. NO HALLUCINATED NUMBERS: Use ONLY the exact numbers that appear in the tables. If information is missing, state clearly: "Based on the provided data table, there is no information about X."
4. MAINTAIN CONTEXT: Pay close attention to categorical columns to filter the exact rows requested.
"""


class LlmRouter:
    """Streaming chat over the configured Ollama instance."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        # Imported lazily so the (heavy) llama_index modules don't load
        # at process start if someone monkey-patches the router for tests.
        from llama_index.llms.ollama import Ollama

        self._model = model
        self._llm = Ollama(
            model=model,
            base_url=base_url,
            request_timeout=timeout,
        )
        logger.info(
            "llm_router_ready backend=ollama model=%s base_url=%s system_prompt_chars=%d",
            model, base_url, len(ENFORCED_SYSTEM_PROMPT),
        )

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    #  Public streaming API
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        messages: Sequence[dict],
    ) -> AsyncIterator[str]:
        """Yields LLM reply deltas as they arrive from Ollama.

        The :data:`ENFORCED_SYSTEM_PROMPT` is **always** placed at index 0
        of the conversation. If the caller already supplied a ``system``
        message (the rendered RAG template), the enforced prompt is
        prepended to its content — producing **one** coherent system
        block instead of two stacked ones (Qwen + small quants behave
        better with a single system message).

        Args:
            messages: list of ``{role, content}`` dicts. ``role`` is one
                      of ``user`` / ``assistant`` / ``system``.

        Raises:
            Whatever llama-index / Ollama raises on transport failure —
            caller wraps into an SSE ``error`` event.
        """
        from llama_index.core.llms import ChatMessage, MessageRole

        merged = self._inject_system_prompt(list(messages))

        role_map = {
            "user":      MessageRole.USER,
            "assistant": MessageRole.ASSISTANT,
            "system":    MessageRole.SYSTEM,
        }

        ll_messages = [
            ChatMessage(
                role=role_map.get(m["role"], MessageRole.USER),
                content=m["content"],
            )
            for m in merged
        ]

        logger.debug(
            "llm_stream_chat n_messages=%d roles=%s system_chars=%d",
            len(ll_messages),
            [m["role"] for m in merged],
            sum(len(m["content"]) for m in merged if m["role"] == "system"),
        )

        gen = await self._llm.astream_chat(ll_messages)
        async for response in gen:
            # `delta` is the new chunk; `message.content` is cumulative.
            if response.delta:
                yield response.delta

    # ------------------------------------------------------------------
    #  Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_system_prompt(messages: list[dict]) -> list[dict]:
        """Force the strict table-handling rules onto the conversation.

        - If the first message is already ``system``: prepend the
          enforced text to its content (one merged block).
        - Otherwise: insert a new ``system`` message at index 0.
        """
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content", "") or ""
            messages[0] = {
                "role":    "system",
                "content": f"{ENFORCED_SYSTEM_PROMPT}\n\n{existing}",
            }
        else:
            messages.insert(0, {"role": "system", "content": ENFORCED_SYSTEM_PROMPT})

        return messages
