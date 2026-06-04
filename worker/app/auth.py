"""Shared-secret API-key dependency.

Every protected route depends on :func:`require_api_key`. The .NET gateway
sets the ``X-Worker-Api-Key`` header on every outbound request — see
``Infrastructure/AiWorker/AiWorkerClient.cs`` on the .NET side.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings

_HEADER_NAME = "X-Worker-Api-Key"

# auto_error=False so we can return our own 401 body shape.
_api_key_header = APIKeyHeader(name=_HEADER_NAME, auto_error=False)


async def require_api_key(
    api_key: Annotated[str | None, Depends(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject requests with missing or wrong ``X-Worker-Api-Key``."""
    if not api_key or api_key != settings.worker_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {_HEADER_NAME} header.",
        )
