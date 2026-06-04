"""LLM backend — v1 routes everything to Ollama (gemma2:2b by default).

Wrapped behind a thin class so swapping in Gemini / OpenAI later is a
single-file change (master plan §3.7, parking lot §7).
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Sequence

logger = logging.getLogger(__name__)


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
        logger.info("llm_router_ready backend=ollama model=%s base_url=%s",
                    model, base_url)

    @property
    def model(self) -> str:
        return self._model

    async def stream_chat(
        self,
        messages: Sequence[dict],
    ) -> AsyncIterator[str]:
        """Yields LLM reply deltas as they arrive from Ollama.

        Args:
            messages: list of dicts with ``role`` (user/assistant/system) and ``content``.

        Raises:
            Whatever llama-index / Ollama raises on transport failure — caller
            wraps these into an SSE ``error`` event.
        """
        from llama_index.core.llms import ChatMessage, MessageRole

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
            for m in messages
        ]

        gen = await self._llm.astream_chat(ll_messages)
        async for response in gen:
            # `delta` is the new chunk; `message.content` is the cumulative reply.
            # We only want the delta so the .NET SSE client can append.
            if response.delta:
                yield response.delta
