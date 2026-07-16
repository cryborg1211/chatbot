"""Tenant-filtered hybrid vector + BM25 search over the ld3_knowledge collection.

⚠ TENANT BOUNDARY enforced here (see master plan §5.5).
The ``department_id`` filter is applied on BOTH the dense prefetch AND the
BM25 sparse-vector prefetch — the worker never returns chunks belonging to
a different department.

Phase 1 retrieval flow (v3 — corrected):
  1. Dense prefetch: bge-m3 cosine on the named ``"dense"`` vector
  2. BM25 sparse prefetch: real Qdrant BM25 via a named SPARSE vector
     (``"bm25"``, fastembed ``Qdrant/bm25`` + server-side ``Modifier.IDF``)
     on Vietnamese word-segmented text (pyvi). NOTE: an earlier version of
     this module tried to query the payload TEXT index directly via
     ``Prefetch(query=<string>, using="")`` — that is invalid; Qdrant's
     TEXT payload index is filter-only (``MatchText``) and cannot be a
     scored query branch. Confirmed by a live 400 Bad Request against a
     real Qdrant 1.18 server. Sparse vectors are the only way to get
     ranked BM25 out of ``query_points``.
  3. RRF fusion (server-side, k tunable) merges the two ranked lists
  4. (Optional) reranker rescores the RRF top-N (see ``reranker.py``)
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Prefetch,
    SparseVector,
)

from .sparse_encoder import SPARSE_VECTOR_NAME

logger = logging.getLogger(__name__)

# How long each chunk snippet shown to the UI may be (chars, not tokens).
SNIPPET_MAX_CHARS = 500

# Default RRF k parameter. 60 is the standard value from the original
# RRF paper (Cormack et al., 2009). Tunable via Settings.
DEFAULT_RRF_K = 60


class RetrievedSource(BaseModel):
    """One Qdrant hit, shaped for the SSE ``sources`` event payload."""

    id:           str                                       # chunk id (Qdrant point id, UUID5)
    document_id:  str                                       # parent document id — used by .NET for citations
    title:        str                                       # original file name
    # Two views of the same chunk, deliberately kept separate:
    #   ``text``    = FULL untruncated chunk — fed to the LLM as CONTEXT (the answer may sit
    #                 anywhere in the chunk, so it must never be truncated for the model).
    #   ``snippet`` = 500-char truncation — for the browser UI citation display only.
    text:         str                                       # full chunk text — LLM context
    snippet:      str                                       # truncated chunk text — UI citation preview
    score:        float = Field(..., description="RRF fusion score")


def _tenant_filter(department_id: str) -> Filter:
    """Build the Qdrant MUST filter for tenant isolation.

    The filter is shared across both prefetch branches so no branch can
    leak cross-tenant data. Used identically in dense and BM25 prefetches.
    """
    return Filter(
        must=[
            FieldCondition(
                key="department_id",
                match=MatchValue(value=department_id),
            )
        ]
    )


class Retriever:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        *,
        rrf_k: int = DEFAULT_RRF_K,
    ):
        self._client = client
        self._collection = collection
        self._rrf_k = rrf_k

    def search(
        self,
        query_vector: list[float],
        query_text_segmented: str,
        department_id: str,
        top_k: int = 10,
        prefetch_limit: int = 50,
    ) -> list[RetrievedSource]:
        """Hybrid search: dense cosine + BM25 sparse, fused via server-side RRF.

        Args:
            query_vector: bge-m3 dense embedding of the user query.
            query_text_segmented: Vietnamese word-segmented query string
                (pyvi). Encoded into a BM25 sparse vector here — must use the
                SAME segmenter as the indexed chunks or keyword overlap breaks.
            department_id: Tenant key — enforced as a MUST filter on BOTH
                prefetch branches. Never None — the constructor raises if
                missing.
            top_k: Final number of results returned to the caller (LLM).
            prefetch_limit: Number of candidates each prefetch retrieves
                before RRF fusion. Higher = better recall, slower.

        Returns:
            Top-k ``RetrievedSource`` hits, scored by RRF fusion.
        """
        if not department_id:
            raise ValueError("department_id is required for tenant-scoped retrieval.")

        tenant = _tenant_filter(department_id)

        # ---- Prefetch 1: Dense vector search (bge-m3 cosine) ----
        dense_prefetch = Prefetch(
            query=query_vector,
            using="dense",
            limit=prefetch_limit,
            filter=tenant,
        )

        # ---- Prefetch 2: BM25 sparse-vector search ----
        # The query text was already segmented by the caller; encode it into
        # the same sparse space as the indexed chunks (fastembed Qdrant/bm25).
        from .sparse_encoder import encode_one
        query_sparse = encode_one(query_text_segmented)
        bm25_prefetch = Prefetch(
            query=SparseVector(indices=query_sparse.indices, values=query_sparse.values),
            using=SPARSE_VECTOR_NAME,
            limit=prefetch_limit,
            filter=tenant,
        )

        # ---- RRF fusion (server-side) ----
        results = self._client.query_points(
            collection_name=self._collection,
            prefetch=[dense_prefetch, bm25_prefetch],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )

        logger.info(
            "hybrid_retrieve dept=%s top_k=%d prefetch=%d hits=%d",
            department_id, top_k, prefetch_limit, len(results.points),
        )

        return [
            RetrievedSource(
                id=str(point.id),
                document_id=_payload_str(point.payload, "document_id", ""),
                title=_payload_str(point.payload, "original_name", "Tài liệu nội bộ"),
                text=_payload_str(point.payload, "text", ""),
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
