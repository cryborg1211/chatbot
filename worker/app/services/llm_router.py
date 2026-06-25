"""LLM backend — provider-agnostic router (Ollama / OpenAI / Anthropic / Gemini).

The concrete backend is built by :func:`build_chat_llm`; provider SDKs are
imported lazily so an uninstalled cloud provider never breaks the default path.

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


SUPPORTED_PROVIDERS: tuple[str, ...] = ("ollama", "openai", "anthropic", "gemini")


def build_chat_llm(
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
    temperature: float = 0.1,
):
    """Build a llama-index streaming chat LLM for ``provider``.

    Provider SDKs are imported lazily, so an uninstalled cloud provider never
    breaks the (Ollama-only) default path. Cloud providers need their package:
    ``llama-index-llms-openai`` / ``-anthropic`` / ``-gemini`` (installed in Phase 4).
    """
    provider = (provider or "ollama").strip().lower()

    if provider == "ollama":
        from llama_index.llms.ollama import Ollama
        return Ollama(
            model=model,
            base_url=base_url or "http://localhost:11434",
            request_timeout=timeout,
            temperature=temperature,
        )
    if provider == "openai":
        from llama_index.llms.openai import OpenAI
        return OpenAI(model=model, api_key=api_key, temperature=temperature, timeout=timeout)
    if provider == "anthropic":
        from llama_index.llms.anthropic import Anthropic
        return Anthropic(model=model, api_key=api_key, temperature=temperature, timeout=timeout)
    if provider == "gemini":
        from llama_index.llms.gemini import Gemini
        return Gemini(model_name=model, api_key=api_key, temperature=temperature)

    raise ValueError(
        f"Unsupported LLM provider: {provider!r}. Expected one of {SUPPORTED_PROVIDERS}."
    )


class LlmRouter:
    """Streaming chat over any supported provider. Provider-agnostic: owns the
    enforced system prompt + streaming; the concrete backend comes from
    :func:`build_chat_llm`. Back-compatible default keeps everything on Ollama."""

    def __init__(
        self,
        model: str,
        provider: str = "ollama",
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        temperature: float = 0.1,
    ):
        self._provider = (provider or "ollama").strip().lower()
        self._model = model
        self._llm = build_chat_llm(
            self._provider,
            model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
        )
        logger.info(
            "llm_router_ready provider=%s model=%s temperature=%.2f system_prompt_chars=%d",
            self._provider, model, temperature, len(ENFORCED_SYSTEM_PROMPT),
        )

    @property
    def provider(self) -> str:
        return self._provider

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
