"""GET /api/llm/status — LLM backend health for the admin AI-settings dashboard.

Read-only. Reports the active provider/model and whether the local Ollama
instance is reachable, plus the list of installed Ollama models (pulled from
Ollama's own ``/api/tags``). Never raises on Ollama being down — it just
reports ``ollama_reachable=false`` so the dashboard can render an offline state.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import require_api_key
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["llm"], dependencies=[Depends(require_api_key)])


@router.get("/llm/status")
async def llm_status(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    base_url = settings.ollama_base_url.rstrip("/")
    reachable = False
    models: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", []) if m.get("name")]
            reachable = True
    except Exception as exc:  # noqa: BLE001 — status probe must never fail the request
        logger.warning("ollama_status_unreachable base_url=%s err=%s", base_url, exc)

    return {
        "provider": "ollama",
        "active_model": settings.ollama_model,
        "base_url": settings.ollama_base_url,
        "ollama_reachable": reachable,
        "installed_models": models,
    }


class LlmProviderRequest(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None


# Substrings that mark a NON-text model (embeddings / audio / image / etc.).
_NON_TEXT_MARKERS: tuple[str, ...] = (
    "embed", "embedding", "whisper", "tts", "audio", "speech", "transcrib",
    "voice", "dall-e", "dalle", "image", "imagen", "vision-", "ocr",
    "moderation", "rerank", "clip", "sora", "veo", "realtime", "live",
    "banana", "video", "diffusion", "computer-use", "search", "similarity",
    "-edit", "edit-", "codex",
)


def _is_text_model(name: str) -> bool:
    """True when `name` looks like a text/chat model (not embeddings/audio/image)."""
    n = name.lower()
    return not any(marker in n for marker in _NON_TEXT_MARKERS)


@router.post("/llm/models")
async def llm_models(
    req: LlmProviderRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Fetch the live model list for a provider, keep only text/chat models.
    Returns ``{ok, models[], error?}`` — never raises. Auth errors (401 etc.)
    are surfaced verbatim, not swallowed."""
    provider = (req.provider or "ollama").strip().lower()
    key = req.api_key or ""

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "ollama":
                resp = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
                if resp.status_code != 200:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                names = [m.get("name", "") for m in resp.json().get("models", [])]

            elif provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code != 200:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                names = [m.get("id", "") for m in resp.json().get("data", [])]

            elif provider == "anthropic":
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
                if resp.status_code != 200:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                names = [m.get("id", "") for m in resp.json().get("data", [])]

            elif provider == "gemini":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": key},
                )
                if resp.status_code != 200:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
                names = [
                    m.get("name", "").split("/")[-1]
                    for m in resp.json().get("models", [])
                    if "generateContent" in (m.get("supportedGenerationMethods") or [])
                ]

            else:
                return {"ok": False, "error": f"Unsupported provider: {provider!r}"}

        models = sorted({n for n in names if n and _is_text_model(n)})
        return {"ok": True, "models": models}

    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_models_failed provider=%s err=%s", provider, exc)
        return {"ok": False, "error": str(exc)[:300]}
