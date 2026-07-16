"""Tests for the hybrid retriever (Phase 1.3, v3 schema).

Guards the two non-negotiable retrieval contracts:
  1. Tenant filter (department_id) is applied to BOTH the dense prefetch AND
     the BM25 sparse-vector prefetch — no branch can leak cross-tenant data.
  2. RRF fusion is actually used (FusionQuery with Fusion.RRF).

Uses a mock Qdrant client that records the exact query_points call so we
can assert on the call shape without a running Qdrant instance.

v3 note: the BM25 branch now queries a named SPARSE vector (fastembed
Qdrant/bm25 + Modifier.IDF) — NOT a payload TEXT index via ``using=""``.
The ``using=""`` design was disproven by a live 400 Bad Request against a
real Qdrant 1.18 server: Qdrant's TEXT payload index is filter-only and
cannot be a scored Prefetch branch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from qdrant_client.models import Fusion, FusionQuery, Prefetch

from app.services.retriever import Retriever, _tenant_filter


# ---------------------------------------------------------------------
#  Mock Qdrant client + result point
# ---------------------------------------------------------------------

def _mock_point(point_id="abc", score=0.9, **payload_overrides):
    """Build a fake Qdrant point with a payload."""
    payload = {
        "document_id": "doc-1",
        "department_id": "IT",
        "text": "Nội dung chunk mẫu về quyết định 1202.",
        "original_name": "quyet_dinh.pdf",
        **payload_overrides,
    }
    return SimpleNamespace(id=point_id, score=score, payload=payload)


def _mock_client(points=None):
    """A mock QdrantClient whose query_points records its call and returns points."""
    client = MagicMock()
    client.query_points.return_value = SimpleNamespace(points=points or [_mock_point()])
    return client


# ---------------------------------------------------------------------
#  Tenant filter on BOTH prefetches
# ---------------------------------------------------------------------

def test_tenant_filter_applied_to_both_prefetches() -> None:
    """The dense AND BM25 prefetch must both carry the department_id filter.

    This is the security-critical assertion: if either branch omits the
    tenant filter, cross-tenant data can leak through RRF fusion.
    """
    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge", rrf_k=60)

    retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="quyết_định",
        department_id="IT",
        top_k=8,
    )

    # Inspect the recorded call.
    call = client.query_points.call_args
    prefetches = call.kwargs["prefetch"]
    assert len(prefetches) == 2, "expected exactly 2 prefetches (dense + BM25)"

    for i, pf in enumerate(prefetches):
        assert pf.filter is not None, f"prefetch {i} has no tenant filter"
        must = pf.filter.must
        assert must, f"prefetch {i} filter has no MUST conditions"
        # The tenant condition must be department_id == "IT".
        tenant_conds = [
            c for c in must
            if getattr(c, "key", None) == "department_id"
        ]
        assert tenant_conds, (
            f"prefetch {i} has no department_id condition — TENANT LEAK RISK"
        )
        assert tenant_conds[0].match.value == "IT"


def test_dense_prefetch_uses_named_dense_vector() -> None:
    """The dense prefetch must use the named 'dense' vector, not the unnamed default."""
    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge")

    retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="test",
        department_id="HR",
    )

    call = client.query_points.call_args
    prefetches = call.kwargs["prefetch"]
    dense_pf = prefetches[0]
    assert dense_pf.using == "dense", (
        f"dense prefetch using={dense_pf.using!r}, expected 'dense'"
    )
    assert dense_pf.query == [0.1] * 5, "dense prefetch lost the query vector"


def test_bm25_prefetch_uses_sparse_vector() -> None:
    """The BM25 prefetch must use the named 'bm25' SPARSE vector.

    Not a payload TEXT index (``using=""``) — that design was disproven
    live against a real Qdrant server (400 Bad Request). Real BM25 requires
    a named sparse vector; Qdrant applies IDF weighting server-side.
    """
    from qdrant_client.models import SparseVector

    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge")

    retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="quyết_định_segmented",
        department_id="HR",
    )

    call = client.query_points.call_args
    prefetches = call.kwargs["prefetch"]
    bm25_pf = prefetches[1]
    assert bm25_pf.using == "bm25", (
        f"BM25 prefetch using={bm25_pf.using!r}, expected 'bm25' (named sparse vector)"
    )
    assert isinstance(bm25_pf.query, SparseVector), (
        f"BM25 prefetch query is {type(bm25_pf.query).__name__}, expected SparseVector"
    )
    assert len(bm25_pf.query.indices) > 0, "sparse vector has no encoded tokens"


def test_rrf_fusion_is_used() -> None:
    """The top-level query must be a FusionQuery with Fusion.RRF."""
    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge", rrf_k=60)

    retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="test",
        department_id="HR",
    )

    call = client.query_points.call_args
    fusion_query = call.kwargs["query"]
    assert isinstance(fusion_query, FusionQuery), (
        f"top-level query is {type(fusion_query).__name__}, not FusionQuery"
    )
    assert fusion_query.fusion == Fusion.RRF, (
        f"fusion={fusion_query.fusion!r}, expected Fusion.RRF"
    )


def test_missing_department_id_raises() -> None:
    """A missing tenant key must raise, never silently fall back to all data."""
    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge")

    try:
        retriever.search(
            query_vector=[0.1] * 5,
            query_text_segmented="test",
            department_id="",
        )
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "department_id" in str(e).lower()


def test_prefetch_limit_passed_through() -> None:
    """The prefetch_limit controls how many candidates each branch retrieves."""
    client = _mock_client()
    retriever = Retriever(client, "ld3_knowledge")

    retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="test",
        department_id="HR",
        prefetch_limit=30,
    )

    call = client.query_points.call_args
    for pf in call.kwargs["prefetch"]:
        assert pf.limit == 30


def test_result_shapes_correct() -> None:
    """Returned RetrievedSource objects map Qdrant payload fields correctly."""
    client = _mock_client(points=[
        _mock_point(point_id="id-1", score=0.95, document_id="doc-A"),
    ])
    retriever = Retriever(client, "ld3_knowledge")

    results = retriever.search(
        query_vector=[0.1] * 5,
        query_text_segmented="test",
        department_id="IT",
    )

    assert len(results) == 1
    src = results[0]
    assert src.id == "id-1"
    assert src.document_id == "doc-A"
    assert src.title == "quyet_dinh.pdf"
    assert src.text == "Nội dung chunk mẫu về quyết định 1202."
    assert src.score == 0.95
    # Snippet is truncated form (under 500 chars here, so unchanged).
    assert src.snippet == src.text


def test_tenant_filter_helper_shape() -> None:
    """_tenant_filter builds a single MUST condition on department_id."""
    f = _tenant_filter("FIN")
    assert f.must is not None
    assert len(f.must) == 1
    cond = f.must[0]
    assert cond.key == "department_id"
    assert cond.match.value == "FIN"
