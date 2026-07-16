"""Reranker ordering and fallback tests.

Existing test_retriever_fusion.py covers the retriever's RRF mechanics.
This file tests the Reranker class itself: score ordering, fallback on
failure, RAM guard behavior, and input edge cases.

All tests mock the CrossEncoder to avoid loading the 2GB model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.reranker import Reranker


@pytest.fixture
def reranker():
    """A Reranker with mocked model load (skip real 2GB download)."""
    r = Reranker(model_name="mock-model", ram_threshold_gb=0.1)
    return r


def _force_load(reranker_obj, scores):
    """Patch the reranker to use a mock CrossEncoder returning given scores."""
    import app.services.reranker as mod

    mock_model = MagicMock()
    mock_model.predict.return_value = scores
    mod._model = mock_model
    mod._model_name = "mock-model"
    reranker_obj._ready = True


# ====================================================================
#  Score ordering
# ====================================================================

def test_rerank_returns_descending_scores(reranker) -> None:
    """Results sorted by cross-encoder score, highest first."""
    _force_load(reranker, [0.2, 0.9, 0.5])
    docs = ["doc A", "doc B", "doc C"]
    ranked = reranker.rerank("query", docs)

    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True), f"Not descending: {scores}"
    assert ranked[0] == (1, 0.9)  # doc B is best
    assert ranked[1] == (2, 0.5)  # doc C second
    assert ranked[2] == (0, 0.2)  # doc A last


def test_rerank_top_k_truncates(reranker) -> None:
    """top_k limits the number of returned results."""
    _force_load(reranker, [0.1, 0.9, 0.5, 0.3, 0.7])
    docs = [f"doc {i}" for i in range(5)]
    ranked = reranker.rerank("query", docs, top_k=3)

    assert len(ranked) == 3
    # Top 3 by score: 0.9 (idx 1), 0.7 (idx 4), 0.5 (idx 2)
    assert ranked[0][0] == 1
    assert ranked[1][0] == 4
    assert ranked[2][0] == 2


def test_rerank_preserves_original_indices(reranker) -> None:
    """Returned indices map back to the original document list positions."""
    _force_load(reranker, [0.3, 0.1, 0.8, 0.6])
    docs = ["alpha", "beta", "gamma", "delta"]
    ranked = reranker.rerank("query", docs)

    indices = [idx for idx, _ in ranked]
    assert set(indices) == {0, 1, 2, 3}, "Some original indices missing"
    # gamma (idx 2, score 0.8) should be first
    assert ranked[0] == (2, 0.8)


# ====================================================================
#  Fallback behavior
# ====================================================================

def test_fallback_on_predict_failure(reranker) -> None:
    """If predict() throws, return original order with decay scores."""
    import app.services.reranker as mod
    mock_model = MagicMock()
    mock_model.predict.side_effect = RuntimeError("OOM")
    mod._model = mock_model
    mod._model_name = "mock-model"
    reranker._ready = True

    docs = ["a", "b", "c"]
    ranked = reranker.rerank("query", docs)

    assert len(ranked) == 3
    indices = [idx for idx, _ in ranked]
    assert indices == [0, 1, 2], "Fallback should preserve original order"


def test_fallback_on_low_ram() -> None:
    """When RAM is below threshold, skip loading and return original order."""
    r = Reranker(model_name="mock-model", ram_threshold_gb=999.0)  # impossibly high
    docs = ["x", "y", "z"]
    ranked = r.rerank("query", docs)

    assert len(ranked) == 3
    indices = [idx for idx, _ in ranked]
    assert indices == [0, 1, 2]


# ====================================================================
#  Edge cases
# ====================================================================

def test_empty_documents() -> None:
    """Empty document list returns empty."""
    r = Reranker()
    assert r.rerank("query", []) == []


def test_single_document(reranker) -> None:
    """Single document returns it at index 0."""
    _force_load(reranker, [0.75])
    ranked = reranker.rerank("query", ["only doc"])
    assert len(ranked) == 1
    assert ranked[0] == (0, 0.75)


def test_identical_scores(reranker) -> None:
    """All-same scores: returns all documents (no crash, no drop)."""
    _force_load(reranker, [0.5, 0.5, 0.5])
    ranked = reranker.rerank("query", ["a", "b", "c"])
    assert len(ranked) == 3
    indices = sorted(idx for idx, _ in ranked)
    assert indices == [0, 1, 2]


# ====================================================================
#  Vietnamese query relevance ordering (mock scores)
# ====================================================================

def test_vietnamese_query_rerank_ordering(reranker) -> None:
    """Simulates a Vietnamese query where the relevant chunk scores highest."""
    _force_load(reranker, [
        0.15,   # irrelevant chunk about agriculture
        0.92,   # chunk containing the answer about QĐ-UBND
        0.35,   # partially relevant chunk
        0.08,   # noise chunk
    ])
    docs = [
        "Kế hoạch phát triển nông nghiệp 2023",
        "Quyết định 33/2020/QĐ-UBND ban hành quy chế quản lý",
        "Báo cáo tổng hợp hoạt động khoa học",
        "Phụ lục đính kèm",
    ]
    ranked = reranker.rerank("Quyết định 33/2020 về nội dung gì?", docs, top_k=2)

    assert ranked[0][0] == 1, "QĐ-UBND chunk should rank first"
    assert ranked[1][0] == 2, "Partially relevant chunk should rank second"
