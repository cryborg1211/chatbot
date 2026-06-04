"""Liveness probe — unauthenticated.

The .NET gateway (or a load balancer) can hit this to verify the worker
process is up. Does NOT verify the embedder is loaded — that would block
the probe during the 30s model boot. For "ready to serve" semantics see
the future `/ready` endpoint (out of scope for Phase 2).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ld3-rag-worker"}
