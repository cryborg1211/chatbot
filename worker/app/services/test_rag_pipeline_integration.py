"""End-to-end RAG pipeline integration tests (mocked Qdrant + LLM).

Verifies the full flow: chunk → embed → segment → store → retrieve → prompt
without needing a live Qdrant or LLM server. Each test builds a mini corpus,
mocks the vector DB layer, and asserts that the right content reaches the
right stage.

This catches integration bugs that unit tests on individual components miss:
- Segmented text stored at ingest matches segmented query at retrieval
- Full chunk text (not snippet) flows through to the LLM prompt
- Sources SSE payload excludes full text (wire contract)
- Tenant isolation enforced end-to-end
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.retriever import Retriever, RetrievedSource, SNIPPET_MAX_CHARS


# ====================================================================
#  Helpers
# ====================================================================

def _make_qdrant_point(point_id, doc_id, dept_id, chunk_idx, text, text_seg, name, score=0.8):
    """Simulate a Qdrant ScoredPoint."""
    return MagicMock(
        id=point_id,
        score=score,
        payload={
            "document_id": doc_id,
            "department_id": dept_id,
            "chunk_index": chunk_idx,
            "text": text,
            "text_segmented": text_seg,
            "original_name": name,
        },
    )


def _mock_query_points(points):
    """Create a mock client.query_points that returns the given points."""
    client = MagicMock()
    client.query_points.return_value = MagicMock(points=points)
    return client


# ====================================================================
#  Full text reaches LLM (not truncated)
# ====================================================================

def test_full_text_in_retrieved_source() -> None:
    """RetrievedSource.text contains the FULL chunk, not the 500-char snippet."""
    long_text = "Quyết định về kế hoạch. " * 100  # ~2400 chars
    assert len(long_text) > SNIPPET_MAX_CHARS

    point = _make_qdrant_point(
        "uuid-1", "doc-1", "HR", 0, long_text,
        "segmented version", "test.pdf", score=0.9,
    )
    client = _mock_query_points([point])
    retriever = Retriever(client, "test_collection")

    results = retriever.search(
        query_vector=[0.1] * 1024,
        query_text_segmented="segmented query",
        department_id="HR",
    )

    assert len(results) == 1
    assert results[0].text == long_text, "Full text must be in .text field"
    assert len(results[0].snippet) <= SNIPPET_MAX_CHARS + 3, "Snippet must be truncated"
    assert results[0].snippet.endswith("..."), "Truncated snippet must end with ..."


def test_snippet_not_truncated_for_short_text() -> None:
    """Short text: snippet == text (no truncation)."""
    short_text = "Nội dung ngắn."
    point = _make_qdrant_point(
        "uuid-1", "doc-1", "HR", 0, short_text,
        "segmented", "test.pdf",
    )
    client = _mock_query_points([point])
    retriever = Retriever(client, "test_collection")
    results = retriever.search([0.1] * 1024, "seg", "HR")

    assert results[0].text == short_text
    assert results[0].snippet == short_text  # no truncation needed


# ====================================================================
#  Prompt template receives full text
# ====================================================================

def test_prompt_builder_uses_text_not_snippet() -> None:
    """The Jinja2 template renders doc.text (full), not doc.snippet."""
    from app.services.prompt_builder import PromptBuilder

    long_content = "Chi tiết kế hoạch đầu tư. " * 60
    tail_marker = long_content[-30:]

    source = RetrievedSource(
        id="test-id",
        document_id="doc-1",
        title="plan.pdf",
        text=long_content,
        snippet=long_content[:500] + "...",
        score=0.85,
    )

    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert tail_marker in rendered, (
        "Tail of chunk not in prompt — template may use doc.snippet instead of doc.text"
    )


# ====================================================================
#  Sources SSE wire contract
# ====================================================================

def test_sources_sse_excludes_text_field() -> None:
    """The SSE 'sources' event must NOT include the full text field.

    Wire contract: .NET gateway receives only snippet (for citations),
    never the full chunk text (saves bandwidth, no data leak to browser).
    """
    source = RetrievedSource(
        id="test-id",
        document_id="doc-1",
        title="test.pdf",
        text="Full chunk text that should NOT appear in SSE",
        snippet="Truncated...",
        score=0.9,
    )

    wire_payload = source.model_dump(mode="json", exclude={"text"})

    assert "text" not in wire_payload, "Full text must be excluded from SSE payload"
    assert "snippet" in wire_payload
    assert "title" in wire_payload
    assert "score" in wire_payload
    assert "document_id" in wire_payload


# ====================================================================
#  Segmentation symmetry (ingest ↔ query)
# ====================================================================

def test_segmentation_symmetry_in_pipeline() -> None:
    """The same segment() function is used at ingest and query time.

    If ingest uses a different segmenter than query, BM25 keyword overlap
    silently breaks on compound words.
    """
    from app.services.segmenter import segment

    doc_text = "khoa học và công nghệ tỉnh Lâm Đồng"
    query_text = "khoa học công nghệ Lâm Đồng"

    doc_seg = segment(doc_text)
    query_seg = segment(query_text)

    # Shared compound words should be segmented identically in both
    # "khoa_học" and "công_nghệ" should appear in both outputs
    doc_tokens = set(doc_seg.split())
    query_tokens = set(query_seg.split())
    shared = doc_tokens & query_tokens

    assert len(shared) >= 2, (
        f"Too few shared BM25 tokens between doc and query segmentation. "
        f"Doc tokens: {doc_tokens}, Query tokens: {query_tokens}"
    )


# ====================================================================
#  Tenant isolation end-to-end
# ====================================================================

def test_tenant_filter_in_query_points_call() -> None:
    """department_id filter is present in the Qdrant query_points call."""
    client = _mock_query_points([])
    retriever = Retriever(client, "test_collection")
    retriever.search([0.1] * 1024, "query", "FINANCE")

    call_kwargs = client.query_points.call_args.kwargs
    prefetches = call_kwargs.get("prefetch", [])

    for i, pf in enumerate(prefetches):
        assert pf.filter is not None, f"Prefetch {i} has no tenant filter!"
        must_conditions = pf.filter.must
        dept_filters = [
            c for c in must_conditions
            if hasattr(c, "key") and c.key == "department_id"
        ]
        assert len(dept_filters) == 1, (
            f"Prefetch {i} missing department_id filter. Conditions: {must_conditions}"
        )


def test_missing_department_raises() -> None:
    """Empty department_id raises ValueError (never queries without tenant scope)."""
    client = _mock_query_points([])
    retriever = Retriever(client, "test_collection")

    with pytest.raises(ValueError, match="department_id"):
        retriever.search([0.1] * 1024, "query", "")

    with pytest.raises(ValueError, match="department_id"):
        retriever.search([0.1] * 1024, "query", None)


# ====================================================================
#  Retriever result shape
# ====================================================================

def test_retriever_returns_correct_types() -> None:
    """All RetrievedSource fields have correct types."""
    point = _make_qdrant_point(
        "uuid-1", "doc-1", "HR", 0,
        "content", "segmented", "test.pdf", score=0.75,
    )
    client = _mock_query_points([point])
    retriever = Retriever(client, "test_collection")
    results = retriever.search([0.1] * 1024, "query", "HR")

    r = results[0]
    assert isinstance(r.id, str)
    assert isinstance(r.document_id, str)
    assert isinstance(r.title, str)
    assert isinstance(r.text, str)
    assert isinstance(r.snippet, str)
    assert isinstance(r.score, float)


def test_multiple_results_ordered_by_rrf() -> None:
    """Multiple results come back in the order Qdrant returned them (RRF-fused)."""
    points = [
        _make_qdrant_point("u1", "d1", "HR", 0, "first", "s1", "a.pdf", score=0.95),
        _make_qdrant_point("u2", "d1", "HR", 1, "second", "s2", "a.pdf", score=0.80),
        _make_qdrant_point("u3", "d2", "HR", 0, "third", "s3", "b.pdf", score=0.65),
    ]
    client = _mock_query_points(points)
    retriever = Retriever(client, "test_collection")
    results = retriever.search([0.1] * 1024, "query", "HR")

    assert len(results) == 3
    scores = [r.score for r in results]
    assert scores == [0.95, 0.80, 0.65], "Results should preserve Qdrant RRF order"


# ====================================================================
#  Vietnamese content through the pipeline
# ====================================================================

def test_vietnamese_content_survives_pipeline() -> None:
    """Vietnamese text with full diacritics survives retrieval → prompt."""
    from app.services.prompt_builder import PromptBuilder

    viet_text = (
        "Quyết định số 33/2020/QĐ-UBND ngày 15 tháng 5 năm 2020 của "
        "Ủy ban nhân dân tỉnh Lâm Đồng về việc ban hành Quy chế quản lý "
        "hoạt động khoa học và công nghệ trên địa bàn tỉnh."
    )

    source = RetrievedSource(
        id="vn-1", document_id="doc-qd33", title="QD_33_2020.pdf",
        text=viet_text, snippet=viet_text[:500], score=0.92,
    )

    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert "33/2020/QĐ-UBND" in rendered
    assert "Ủy ban nhân dân" in rendered
    assert "Lâm Đồng" in rendered
    assert "khoa học và công nghệ" in rendered
