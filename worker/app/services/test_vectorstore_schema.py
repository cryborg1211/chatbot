"""VectorStore schema v3 tests.

Verifies the collection schema (named dense vector + named BM25 sparse
vector with IDF modifier), deterministic point IDs, and payload structure —
all via mocked Qdrant client (no server).

v3 replaced the v2 ``text_segmented`` TEXT payload index with a real sparse
vector: Qdrant's payload TEXT index is filter-only and cannot be scored/
ranked inside query_points — confirmed by a live 400 Bad Request against a
real Qdrant 1.18 server when the v2 design was eval-tested.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.vectorstore import VectorStore


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])
    return client


@pytest.fixture
def store(mock_client):
    return VectorStore(mock_client, "test_collection", vector_size=1024)


# ====================================================================
#  Collection creation — schema v2
# ====================================================================

def test_ensure_collection_creates_named_dense_vector(store, mock_client) -> None:
    """Schema v2 uses a NAMED vector 'dense', not the old unnamed single vector."""
    store.ensure_collection()

    create_call = mock_client.create_collection.call_args
    assert create_call is not None
    vectors_config = create_call.kwargs.get("vectors_config") or create_call[1].get("vectors_config")
    assert "dense" in vectors_config, "Missing named 'dense' vector in schema"
    assert vectors_config["dense"].size == 1024
    assert "cosine" in str(vectors_config["dense"].distance).lower()


def test_ensure_collection_creates_payload_indexes(store, mock_client) -> None:
    """Schema v3 indexes department_id and document_id (tenant + doc lookups)."""
    store.ensure_collection()

    index_calls = mock_client.create_payload_index.call_args_list
    indexed_fields = {c.kwargs.get("field_name") or c[1].get("field_name") for c in index_calls}
    assert "department_id" in indexed_fields, "Missing department_id index"
    assert "document_id" in indexed_fields, "Missing document_id index"


def test_ensure_collection_creates_sparse_bm25_vector(store, mock_client) -> None:
    """Schema v3 creates a named 'bm25' sparse vector with the IDF modifier.

    Real server-side BM25 requires a sparse vector, not a payload TEXT
    index — Qdrant's TEXT index only supports filter-only MatchText, it
    cannot be a scored query_points branch.
    """
    from qdrant_client.models import Modifier

    store.ensure_collection()

    create_call = mock_client.create_collection.call_args
    sparse_config = create_call.kwargs.get("sparse_vectors_config") or create_call[1].get("sparse_vectors_config")
    assert sparse_config is not None, "Missing sparse_vectors_config in schema"
    assert "bm25" in sparse_config, "Missing named 'bm25' sparse vector"
    assert sparse_config["bm25"].modifier == Modifier.IDF, (
        "bm25 sparse vector must use Modifier.IDF for real BM25 scoring"
    )


def test_ensure_collection_skips_if_exists(mock_client) -> None:
    """If collection already exists, don't recreate."""
    existing = MagicMock()
    existing.name = "test_collection"
    mock_client.get_collections.return_value = MagicMock(collections=[existing])

    store = VectorStore(mock_client, "test_collection", vector_size=1024)
    store.ensure_collection()

    mock_client.create_collection.assert_not_called()


# ====================================================================
#  Deterministic point IDs
# ====================================================================

def test_point_id_deterministic() -> None:
    """Same (document_id, chunk_index) always produces the same UUID5."""
    id1 = VectorStore._point_id("doc-abc", 0)
    id2 = VectorStore._point_id("doc-abc", 0)
    assert id1 == id2


def test_point_id_differs_for_different_chunks() -> None:
    """Different chunk indices produce different IDs."""
    id0 = VectorStore._point_id("doc-abc", 0)
    id1 = VectorStore._point_id("doc-abc", 1)
    assert id0 != id1


def test_point_id_differs_for_different_documents() -> None:
    """Different documents produce different IDs even at same index."""
    idA = VectorStore._point_id("doc-A", 0)
    idB = VectorStore._point_id("doc-B", 0)
    assert idA != idB


def test_point_id_is_valid_uuid() -> None:
    """Generated ID is a valid UUID string."""
    point_id = VectorStore._point_id("test-doc", 42)
    parsed = uuid.UUID(point_id)  # raises if invalid
    assert str(parsed) == point_id


# ====================================================================
#  Upsert — payload structure + segmentation
# ====================================================================

def test_upsert_stores_raw_text_and_sparse_bm25_vector(store, mock_client) -> None:
    """Each point has payload 'text' (raw) and a 'bm25' sparse vector (not a payload field)."""
    chunks = ["Khoa học và công nghệ Lâm Đồng"]
    vectors = [[0.1] * 1024]

    with patch("app.services.vectorstore.VectorStore._point_id", return_value="test-uuid"):
        store.upsert_chunks("doc-1", "IT", "test.pdf", chunks, vectors)

    upsert_call = mock_client.upsert.call_args
    points = upsert_call.kwargs.get("points") or upsert_call[1].get("points")
    assert len(points) == 1

    payload = points[0].payload
    assert "text" in payload, "Missing raw text field"
    assert "text_segmented" not in payload, "v2 text_segmented payload field should be gone"
    assert payload["text"] == chunks[0], "Raw text should be unmodified"

    vector = points[0].vector
    assert "bm25" in vector, "Missing named bm25 sparse vector"
    assert len(vector["bm25"].indices) > 0, "BM25 sparse vector should not be empty"


def test_upsert_uses_named_dense_vector(store, mock_client) -> None:
    """Dense vector stored under the 'dense' key, not as unnamed."""
    chunks = ["test"]
    vectors = [[0.5] * 1024]

    store.upsert_chunks("doc-1", "IT", "test.pdf", chunks, vectors)

    points = mock_client.upsert.call_args.kwargs.get("points") or \
             mock_client.upsert.call_args[1].get("points")
    assert "dense" in points[0].vector, "Vector should be stored under 'dense' key"


def test_upsert_payload_has_all_required_fields(store, mock_client) -> None:
    """Payload contains all fields needed by retriever and admin queries."""
    store.upsert_chunks("doc-1", "HR", "report.pdf", ["content"], [[0.1] * 1024])

    points = mock_client.upsert.call_args.kwargs.get("points") or \
             mock_client.upsert.call_args[1].get("points")
    payload = points[0].payload

    required = {"document_id", "department_id", "chunk_index", "text", "original_name"}
    assert required.issubset(payload.keys()), f"Missing fields: {required - payload.keys()}"
    assert payload["document_id"] == "doc-1"
    assert payload["department_id"] == "HR"
    assert payload["original_name"] == "report.pdf"
    assert payload["chunk_index"] == 0


def test_upsert_accepts_presegmented_sparse_vectors(store, mock_client) -> None:
    """When sparse_vectors is provided, use those instead of auto-encoding."""
    from app.services.sparse_encoder import SparseVectorResult

    chunks = ["khoa học công nghệ"]
    vectors = [[0.1] * 1024]
    presparse = [SparseVectorResult(indices=[1, 2, 3], values=[0.5, 0.5, 0.5])]

    store.upsert_chunks("doc-1", "IT", "test.pdf", chunks, vectors, sparse_vectors=presparse)

    points = mock_client.upsert.call_args.kwargs.get("points") or \
             mock_client.upsert.call_args[1].get("points")
    bm25_vec = points[0].vector["bm25"]
    assert list(bm25_vec.indices) == [1, 2, 3]
    assert list(bm25_vec.values) == [0.5, 0.5, 0.5]


def test_upsert_chunk_vector_length_mismatch_raises(store) -> None:
    """Mismatched chunks/vectors lengths raise ValueError."""
    with pytest.raises(ValueError, match="mismatch"):
        store.upsert_chunks("doc-1", "IT", "test.pdf", ["a", "b"], [[0.1] * 1024])


def test_upsert_empty_chunks_returns_zero(store, mock_client) -> None:
    """Empty chunks list returns 0 and does not call Qdrant."""
    result = store.upsert_chunks("doc-1", "IT", "test.pdf", [], [])
    assert result == 0
    mock_client.upsert.assert_not_called()


# ====================================================================
#  Delete
# ====================================================================

def test_delete_document_uses_filter(store, mock_client) -> None:
    """delete_document uses payload filter, not point-id enumeration."""
    mock_client.delete.return_value = MagicMock(status="completed")
    store.delete_document("doc-123")

    delete_call = mock_client.delete.call_args
    selector = delete_call.kwargs.get("points_selector") or delete_call[1].get("points_selector")
    assert selector is not None, "Should use FilterSelector, not point IDs"
