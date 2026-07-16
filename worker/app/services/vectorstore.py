"""Qdrant wrapper for the `ld3_knowledge` collection.

Schema v3 (Phase 1 — hybrid retrieval, corrected):
  • Dense vector:  1024 (bge-m3, named vector key ``"dense"``), Cosine
  • Sparse vector: named ``"bm25"`` (fastembed ``Qdrant/bm25`` term-frequency
    vectors + ``Modifier.IDF`` — Qdrant's documented BM25 hybrid-search
    pattern). Replaces the v2 ``text_segmented`` TEXT payload index, which
    was a design error: Qdrant's payload TEXT index is FILTER-ONLY
    (``MatchText`` boolean condition) — it cannot be a scored/ranked branch
    inside ``query_points``/``Prefetch``. There is no "text query" type in
    Qdrant's Query API. Real server-side BM25 requires a sparse vector.
  • Payload:       {document_id, department_id, chunk_index, text, original_name}
    (``text_segmented`` payload field dropped — no longer needed once BM25
    lives in a sparse vector; ``text`` still holds the raw chunk for the LLM.)
  • Payload index: ``department_id`` (KEYWORD), ``document_id`` (KEYWORD)
  • Point id:      uuid5(NAMESPACE_OID, f"{document_id}:{chunk_index}")
                   → deterministic so retries overwrite in place (idempotent).

Named vectors allow Qdrant 1.10+ to run server-side RRF fusion between the
dense ``"dense"`` vector and the sparse ``"bm25"`` vector in a single
``query_points`` call with two prefetches.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from .sparse_encoder import SPARSE_VECTOR_NAME, SparseVectorResult

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, vector_size: int):
        self._client = client
        self._collection = collection
        self._vector_size = vector_size

    # ------------------------------------------------------------------
    #  Collection lifecycle
    # ------------------------------------------------------------------

    def ensure_collection(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            logger.info("collection_exists name=%s", self._collection)
            return

        logger.info(
            "creating_collection_v3 name=%s dim=%s (named dense + sparse bm25/IDF)",
            self._collection, self._vector_size,
        )
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                "dense": VectorParams(
                    size=self._vector_size,
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: SparseVectorParams(
                    modifier=Modifier.IDF,
                ),
            },
        )

        # Payload indexes — REQUIRED for fast tenant filtering at query time.
        for field, schema in [
            ("department_id", PayloadSchemaType.KEYWORD),
            ("document_id", PayloadSchemaType.KEYWORD),
        ]:
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=schema,
            )

    # ------------------------------------------------------------------
    #  Write path
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        document_id: str,
        department_id: str,
        original_name: str,
        chunks: list[str],
        vectors: list[list[float]],
        *,
        sparse_vectors: list[SparseVectorResult] | None = None,
    ) -> int:
        """Upsert chunks into the v3 collection with a real BM25 sparse vector.

        Args:
            sparse_vectors: Pre-computed BM25 sparse vectors (one per chunk,
                from ``sparse_encoder.encode()`` over the pyvi-segmented
                text). If None, computed on the fly (segment + encode) so
                existing callers that only pass dense vectors keep working.
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks ({len(chunks)}) and vectors ({len(vectors)}) length mismatch."
            )
        if not chunks:
            return 0

        if sparse_vectors is None:
            # Lazy import to avoid circular dependency at module level.
            from .segmenter import segment as do_segment
            from .sparse_encoder import encode as do_sparse_encode
            sparse_vectors = do_sparse_encode([do_segment(t) for t in chunks])

        points = []
        for idx, (text, vec, sv) in enumerate(zip(chunks, vectors, sparse_vectors)):
            points.append(
                PointStruct(
                    id=self._point_id(document_id, idx),
                    vector={
                        "dense": vec,
                        SPARSE_VECTOR_NAME: SparseVector(
                            indices=sv.indices, values=sv.values,
                        ),
                    },
                    payload={
                        "document_id":     document_id,
                        "department_id":   department_id,
                        "chunk_index":     idx,
                        "text":            text,           # raw — fed to the LLM
                        "original_name":   original_name,
                    },
                )
            )

        self._client.upsert(
            collection_name=self._collection,
            points=points,
            wait=True,   # block until Qdrant durably persists
        )
        return len(points)

    def delete_document(self, document_id: str) -> None:
        """Remove every chunk belonging to a document (idempotent)."""
        self.delete_by_payload(field="document_id", value=document_id)

    def delete_by_payload(self, field: str, value: str) -> str:
        """
        Delete every point whose payload ``[field] == value`` — in a SINGLE
        Qdrant round trip. The server walks its own indexes to find the
        matching points; we never enumerate ids client-side.

        Args:
            field: Payload key (e.g. ``document_id`` or ``original_name``).
                   Must be one of the indexed keys for fast deletes.
            value: Exact value to match.

        Returns:
            Qdrant operation status as a string (e.g. ``"completed"``).
        """
        op = self._client.delete(
            collection_name=self._collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(
                        key=field,
                        match=MatchValue(value=value),
                    )]
                )
            ),
            wait=True,
        )
        return str(getattr(op, "status", op))

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _point_id(document_id: str, chunk_index: int) -> str:
        """Deterministic UUID5 — same (doc, idx) → same point id → idempotent."""
        return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{document_id}:{chunk_index}"))
