"""Qdrant wrapper for the `ld3_knowledge` collection.

Schema (locked — see master plan §2.7):
  • Vector size: 1024 (bge-m3 dense)
  • Distance:    Cosine
  • Payload:     {document_id, department_id, chunk_index, text, original_name}
  • Payload index: `department_id` (KEYWORD), `document_id` (KEYWORD)
  • Point id:    uuid5(NAMESPACE_OID, f"{document_id}:{chunk_index}")
                 → deterministic so retries overwrite in place (idempotent).
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
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

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
            "creating_collection name=%s dim=%s",
            self._collection, self._vector_size,
        )
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(
                size=self._vector_size,
                distance=Distance.COSINE,
            ),
        )

        # Payload indexes — REQUIRED for fast tenant filtering at query time.
        for field in ("department_id", "document_id"):
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
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
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks ({len(chunks)}) and vectors ({len(vectors)}) length mismatch."
            )
        if not chunks:
            return 0

        points = [
            PointStruct(
                id=self._point_id(document_id, idx),
                vector=vec,
                payload={
                    "document_id":   document_id,
                    "department_id": department_id,
                    "chunk_index":   idx,
                    "text":          text,
                    "original_name": original_name,
                },
            )
            for idx, (text, vec) in enumerate(zip(chunks, vectors))
        ]

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
