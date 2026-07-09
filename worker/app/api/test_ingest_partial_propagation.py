"""Fork 2 — partial-ingest signal propagation test (worker-side hop).

Mocks ``load_documents`` (returns a partial ``DoclingResult``), the chunker,
embedder and vector store, then calls ``_run_pipeline`` directly and asserts the
returned ``_PipelineResult`` carries ``partial``/``partial_reason`` through to the
point where ``IngestResponse`` is built. Deterministic once mocked — Hybrid tier.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api import ingest
from app.api.ingest import _PipelineResult, _run_pipeline
from app.services.loader import DoclingResult


def test_partial_flag_propagates_to_ingest_response(monkeypatch) -> None:
    partial_doc = DoclingResult(
        text="một phần nội dung",
        docling_doc=None,
        metadata={"source": "scan.pdf"},
        partial=True,
        partial_reason="Thiếu 1 nhóm trang: 11-20",
    )

    monkeypatch.setattr(ingest, "load_documents", lambda *_a, **_k: [partial_doc])
    monkeypatch.setattr(
        ingest, "prepend_document_context_to_chunks", lambda texts, _name: texts
    )

    # Mock collaborators: chunker returns one node; embedder returns one vector;
    # vector store reports 1 chunk upserted.
    fake_node = SimpleNamespace(get_content=lambda: "một phần nội dung")
    chunker = SimpleNamespace(split=lambda _docs: [fake_node])
    embedder = SimpleNamespace(encode=lambda texts: [[0.0]])
    vector_store = SimpleNamespace(upsert_chunks=lambda **_k: 1)

    result = _run_pipeline(
        file_bytes=b"%PDF-fake",
        mime_type="application/pdf",
        original_name="scan.pdf",
        document_id="00000000-0000-0000-0000-000000000001",
        department_id="IT",
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert isinstance(result, _PipelineResult)
    assert result.chunk_count == 1
    assert result.partial is True
    assert result.partial_reason == "Thiếu 1 nhóm trang: 11-20"


def test_non_partial_document_yields_partial_false(monkeypatch) -> None:
    """A fully-successful DoclingResult keeps partial=False (no false positives)."""
    ok_doc = DoclingResult(
        text="đầy đủ nội dung",
        docling_doc=None,
        metadata={"source": "clean.pdf"},
    )

    monkeypatch.setattr(ingest, "load_documents", lambda *_a, **_k: [ok_doc])
    monkeypatch.setattr(
        ingest, "prepend_document_context_to_chunks", lambda texts, _name: texts
    )
    fake_node = SimpleNamespace(get_content=lambda: "đầy đủ nội dung")
    chunker = SimpleNamespace(split=lambda _docs: [fake_node])
    embedder = SimpleNamespace(encode=lambda texts: [[0.0]])
    vector_store = SimpleNamespace(upsert_chunks=lambda **_k: 3)

    result = _run_pipeline(
        file_bytes=b"%PDF",
        mime_type="application/pdf",
        original_name="clean.pdf",
        document_id="00000000-0000-0000-0000-000000000002",
        department_id="IT",
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
    )

    assert result.partial is False
    assert result.partial_reason is None
