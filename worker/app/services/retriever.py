"""Tenant-filtered vector search over the ld3_knowledge collection.

⚠ TENANT BOUNDARY enforced here (see master plan §5.5).
The ``department_id`` is taken from the request and applied as a MUST
filter on the Qdrant query — the worker never returns chunks belonging
to a different department, regardless of what the .NET gateway sends.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

logger = logging.getLogger(__name__)

# How long each chunk snippet shown to the LLM may be (chars, not tokens).
SNIPPET_MAX_CHARS = 500


class RetrievedSource(BaseModel):
    """One Qdrant hit, shaped for the SSE ``sources`` event payload."""

    id:           str                                       # chunk id (Qdrant point id, UUID5)
    document_id:  str                                       # parent document id — used by .NET for citations
    title:        str                                       # original file name
    snippet:      str                                       # truncated chunk text — fed to the LLM as context
    score:        float = Field(..., description="Cosine similarity 0..1")


class Retriever:
    def __init__(self, client: QdrantClient, collection: str):
        self._client = client
        self._collection = collection

    def search(
        self,
        query_vector: list[float],
        department_id: str,
        top_k: int = 5,
    ) -> list[RetrievedSource]:
        """Returns the top-k chunks scoped to the given department."""
        if not department_id:
            # Defensive — should never happen because Pydantic enforces min_length=2,
            # but a missing tenant key MUST never silently fall back to "all data".
            raise ValueError("department_id is required for tenant-scoped retrieval.")

        results = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="department_id",
                        match=MatchValue(value=department_id),
                    )
                ]
            ),
            limit=top_k,
            with_payload=True,
        )

        logger.info(
            "retrieve dept=%s top_k=%d hits=%d",
            department_id, top_k, len(results.points),
        )

        return [
            RetrievedSource(
                id=str(point.id),
                document_id=_payload_str(point.payload, "document_id", ""),
                title=_payload_str(point.payload, "original_name", "Tài liệu nội bộ"),
                snippet=_truncate(_payload_str(point.payload, "text", ""), SNIPPET_MAX_CHARS),
                score=float(point.score),
            )
            for point in results.points
        ]


# ---------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------

def _payload_str(payload: dict | None, key: str, default: str) -> str:
    if not payload:
        return default
    val = payload.get(key)
    return str(val) if val is not None else default


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "..."
