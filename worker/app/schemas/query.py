"""Pydantic wire models for the /api/query endpoint.

Request comes in as JSON. Response is SSE so events are not modelled
here — see ``app/api/query.py`` for the event envelopes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryItem(BaseModel):
    """One prior turn of the chat (last N messages sent by .NET)."""

    role: Literal["user", "assistant", "system"]
    content: str


class QueryRequest(BaseModel):
    """Body of ``POST /api/query``.

    snake_case names match the .NET ``QueryRequest`` record after it's
    serialised through ``JsonNamingPolicy.SnakeCaseLower``.
    """

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(..., min_length=1, max_length=4000)

    # Tenant key — never trust this from the browser. The .NET gateway
    # injects it from the authenticated principal's claim.
    department_id: str = Field(..., min_length=2, max_length=20)

    history: list[HistoryItem] = Field(default_factory=list)

    user_id: str | None = Field(default=None, description="For audit logging only.")
