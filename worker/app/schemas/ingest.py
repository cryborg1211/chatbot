"""Pydantic wire-format models for the /ingest endpoint.

See master plan §2.6 for the multipart request shape — request fields
ride as Form/UploadFile parameters on the route handler, so no Pydantic
request model is needed. Only the response is modelled here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IngestResponse(BaseModel):
    """Response body for ``POST /ingest``.

    Same shape on HTTP 200 (success) and HTTP 422 (parse / unsupported /
    empty); discriminate by :attr:`status`.
    """

    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(..., description="Echoed back from the request.")
    status: Literal["success", "failed"]
    chunk_count: int = Field(0, ge=0, description="Number of points upserted to Qdrant.")
    elapsed_ms: int = Field(..., ge=0)

    # Partial-ingest signal (success path): some pages/batches were dropped
    # during parsing (e.g. OCR retry still missing pages). Additive + defaulted
    # so old .NET clients that don't read these fields keep working.
    partial: bool = Field(
        False,
        description="True when some pages/batches were dropped during parsing "
                    "(e.g. OCR retry still missing pages).",
    )
    partial_reason: str | None = Field(
        None,
        description="Human-readable (Vietnamese) summary of what was dropped, "
                    "when partial is True.",
    )

    # Populated only when status == "failed".
    error_code: str | None = None
    message: str | None = None
