"""Pydantic wire-format models for the /ingest endpoint.

See master plan §2.6 for the multipart request shape — request fields
ride as Form/UploadFile parameters on the route handler, so no Pydantic
request model is needed. Only the response is modelled here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PageRoute(BaseModel):
    """One page's ingestion manifest entry (Phase 0).

    Lets an operator see, per page, how it was routed (digital vs OCR) and
    whether its content made it into the index. ``dropped=True`` is a hole
    in the vector space that no downstream reranker can recover.
    """

    page: int = Field(..., ge=1, description="1-based page number.")
    density: Literal["text", "scan"] = Field(
        ..., description="Text-layer density class: 'text' (digital) or 'scan' (image-only)."
    )
    chars: int = Field(0, ge=0, description="Approximate chars extracted (audit signal).")
    ocr_used: bool = Field(False, description="True if the OCR converter parsed this page.")
    dropped: bool = Field(
        False,
        description="True if this page's content was NOT ingested (OOM, parse error, "
                    "or low-RAM skip). Such pages are permanent holes in the index.",
    )


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

    # Phase 0 per-page ingestion manifest. Empty for non-PDF sources. Additive
    # + defaulted so old .NET clients keep working.
    page_routes: list[PageRoute] = Field(
        default_factory=list,
        description="Per-page routing manifest (PDF only): how each page was parsed "
                    "and whether it was dropped.",
    )

    # Populated only when status == "failed".
    error_code: str | None = None
    message: str | None = None
