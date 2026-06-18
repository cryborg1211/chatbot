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
