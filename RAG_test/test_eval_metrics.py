"""Unit tests for the retrieval eval metric implementations.

The handoff doc claimed these were "unit-tested" — they were not; no test
file existed. That gap let a real bug ship: recall_at_k and ndcg_at_k both
summed raw occurrences instead of deduping by unique document, so a
chunk-level retriever returning 3 chunks from the SAME relevant document
within top-k pushed recall@5 to 2.143 and nDCG@5 to 1.367 — both
impossible for metrics bounded in [0, 1]. Fixed by crediting each relevant
document only once (first/highest-ranked occurrence).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_retrieval import recall_at_k, reciprocal_rank, ndcg_at_k, evaluate


# ====================================================================
#  recall_at_k
# ====================================================================

def test_recall_perfect_match() -> None:
    """Single relevant doc retrieved at rank 1 -> recall 1.0."""
    assert recall_at_k(["docA"], {"docA"}, k=5) == 1.0


def test_recall_no_match() -> None:
    """No relevant doc in top-k -> recall 0.0."""
    assert recall_at_k(["docB", "docC"], {"docA"}, k=5) == 0.0


def test_recall_never_exceeds_one_with_duplicate_chunks() -> None:
    """Regression: 3 chunks from the SAME relevant doc must not push recall > 1.0.

    This is the exact bug found in live eval: a chunk-level retriever
    returns multiple hits from one document; document-level recall must
    still cap at 1.0 (that document counts once, not three times).
    """
    retrieved = ["docA", "docA", "docA", "docB", "docC"]
    result = recall_at_k(retrieved, {"docA"}, k=5)
    assert result == 1.0, f"recall must cap at 1.0, got {result}"
    assert result <= 1.0


def test_recall_multiple_relevant_docs_partial() -> None:
    """2 of 3 relevant docs found -> recall 2/3."""
    retrieved = ["docA", "docX", "docB", "docY"]
    result = recall_at_k(retrieved, {"docA", "docB", "docC"}, k=4)
    assert abs(result - 2 / 3) < 1e-9


def test_recall_respects_k_cutoff() -> None:
    """Relevant doc outside top-k is not counted."""
    retrieved = ["docX", "docY", "docZ", "docA"]
    assert recall_at_k(retrieved, {"docA"}, k=3) == 0.0
    assert recall_at_k(retrieved, {"docA"}, k=4) == 1.0


def test_recall_empty_relevant_set() -> None:
    """No ground truth -> 0.0 (not a division error)."""
    assert recall_at_k(["docA"], set(), k=5) == 0.0


# ====================================================================
#  reciprocal_rank
# ====================================================================

def test_rr_first_position() -> None:
    assert reciprocal_rank(["docA", "docB"], {"docA"}) == 1.0


def test_rr_second_position() -> None:
    assert reciprocal_rank(["docB", "docA"], {"docA"}) == 0.5


def test_rr_not_found() -> None:
    assert reciprocal_rank(["docB", "docC"], {"docA"}) == 0.0


def test_rr_duplicate_first_occurrence_wins() -> None:
    """RR uses the first (best-ranked) occurrence, unaffected by later duplicates."""
    result = reciprocal_rank(["docB", "docA", "docA", "docA"], {"docA"})
    assert result == 0.5  # rank 2, not diluted by duplicates at 3/4


# ====================================================================
#  ndcg_at_k
# ====================================================================

def test_ndcg_perfect_ranking() -> None:
    """All relevant docs at the top -> nDCG 1.0."""
    result = ndcg_at_k(["docA", "docB"], {"docA", "docB"}, k=5)
    assert abs(result - 1.0) < 1e-9


def test_ndcg_no_relevant_found() -> None:
    assert ndcg_at_k(["docX", "docY"], {"docA"}, k=5) == 0.0


def test_ndcg_never_exceeds_one_with_duplicate_chunks() -> None:
    """Regression: duplicate chunks from the same doc must not push nDCG > 1.0."""
    retrieved = ["docA", "docA", "docA", "docB", "docC"]
    result = ndcg_at_k(retrieved, {"docA"}, k=5)
    assert result <= 1.0 + 1e-9, f"nDCG must not exceed 1.0, got {result}"
    assert abs(result - 1.0) < 1e-9  # single relevant doc at rank 1 -> perfect


def test_ndcg_worse_ranking_scores_lower() -> None:
    """Relevant doc further down the list scores lower than at rank 1."""
    top1 = ndcg_at_k(["docA", "docX", "docY"], {"docA"}, k=3)
    top3 = ndcg_at_k(["docX", "docY", "docA"], {"docA"}, k=3)
    assert top1 > top3


def test_ndcg_bounded_in_zero_one_range() -> None:
    """nDCG is always in [0, 1] regardless of duplicate or relevant-set size."""
    cases = [
        (["docA", "docA", "docB", "docB"], {"docA", "docB"}, 4),
        (["docC", "docA", "docA", "docA"], {"docA"}, 4),
        ([], {"docA"}, 5),
    ]
    for retrieved, relevant, k in cases:
        result = ndcg_at_k(retrieved, relevant, k)
        assert 0.0 <= result <= 1.0 + 1e-9, f"nDCG out of bounds: {result} for {retrieved}"


# ====================================================================
#  evaluate() aggregation — all metrics stay bounded
# ====================================================================

def test_evaluate_aggregate_metrics_bounded() -> None:
    """Aggregate recall/MRR/nDCG across queries all stay within [0, 1]."""
    retrieved_lists = [
        ["docA", "docA", "docA", "docB", "docC"],  # duplicate-heavy, like real eval
        ["docX", "docY", "docZ"],
        ["docB", "docB"],
    ]
    relevant_sets = [{"docA"}, {"docQ"}, {"docB"}]

    result = evaluate(retrieved_lists, relevant_sets, k=5)

    assert 0.0 <= result["recall@5"] <= 1.0, f"recall@5 out of bounds: {result['recall@5']}"
    assert 0.0 <= result["mrr"] <= 1.0, f"mrr out of bounds: {result['mrr']}"
    assert 0.0 <= result["ndcg@5"] <= 1.0, f"ndcg@5 out of bounds: {result['ndcg@5']}"
    assert result["n"] == 3


def test_evaluate_empty_input() -> None:
    """No queries -> all metrics 0.0, no crash."""
    result = evaluate([], [], k=5)
    assert result["n"] == 0
    assert result["recall@5"] == 0.0
    assert result["mrr"] == 0.0
