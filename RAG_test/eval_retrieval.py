"""Vietnamese retrieval evaluation harness (Phase 1.7).

Measures retrieval quality across configurations:
  - dense-only (baseline): pure bge-m3 cosine
  - hybrid (RRF): dense + BM25 fused
  - hybrid + reranker: cross-encoder rescore

Metrics: recall@k, MRR (Mean Reciprocal Rank), nDCG.

Requires a running Qdrant (1.10+) with the v2 collection populated. Run:
    cd worker
    python -m RAG_test.eval_retrieval

The labeled query set references documents by filename. Each query maps to
the set of source documents that SHOULD be retrieved (document-level recall).
Extend the EVAL_QUERIES list with more labeled examples as the corpus grows.
"""

from __future__ import annotations

import sys
import os
import math
from pathlib import Path

# Fix Windows console encoding for Vietnamese text
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add worker to path so we can import app modules
repo_root = Path(__file__).resolve().parent.parent
worker_dir = repo_root / "worker"
sys.path.insert(0, str(worker_dir))


# ---------------------------------------------------------------------
#  Labeled Vietnamese eval set (document-level relevance).
#  Each entry: (query, set_of_relevant_filenames)
#  Extend this with more labeled queries as you validate the system.
#  These reference the 4 real docs in RAG_test/doc/.
# ---------------------------------------------------------------------

EVAL_QUERIES: list[tuple[str, set[str]]] = [
    (
        "Quyết định 1202 về kết quả hoạt động khoa học và công nghệ",
        {"Quyet dinh 1202_QD_BKHCNCGCN.pdf"},
    ),
    (
        "kế hoạch ứng dụng kết quả hoạt động khoa học công nghệ",
        {"Quyet dinh 1202_QD_BKHCNCGCN.pdf"},
    ),
    (
        "báo cáo thống kê khoa học công nghệ năm 2023",
        {"VB bao cao thong ke khoa hoc cong nghe 2023trinh.doc"},
    ),
    (
        "đề xuất đầu tư dự án trung tâm thương mại",
        {"BC de xuat dau tu du an TTTDC.doc"},
    ),
    (
        "báo cáo tài chính 6 tháng đầu năm 2023",
        {"665 boo coo 389 6 thang nam 2023.signed.signed.signed.pdf"},
    ),
    (
        "1202/QĐ-BKHCNCGCN",
        {"Quyet dinh 1202_QD_BKHCNCGCN.pdf"},
    ),
    (
        "dự án trung tâm thương mại dịch vụ",
        {"BC de xuat dau tu du an TTTDC.doc"},
    ),
]


# ---------------------------------------------------------------------
#  Metric implementations
# ---------------------------------------------------------------------

def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant docs found in the top-k retrieved (document-level).

    Deduped by unique document: a chunk-level retriever can return several
    chunks from the SAME document within top-k, so counting raw occurrences
    (not unique docs) can push this above 1.0 — always intersect against the
    set of unique documents seen, never sum raw hits.
    """
    if not relevant:
        return 0.0
    top_k_docs = set(retrieved[:k])
    hits = len(top_k_docs & relevant)
    return hits / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1/rank of the first relevant result, 0 if none."""
    for i, r in enumerate(retrieved, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at k (binary, document-level relevance).

    Each relevant document contributes gain only once — credited at its
    FIRST (highest-ranked) occurrence. Without this, a chunk-level retriever
    returning 3 chunks from the same relevant document within top-k would
    triple-count that document's gain and push nDCG above 1.0.
    """
    dcg = 0.0
    credited: set[str] = set()
    for i, r in enumerate(retrieved[:k], start=1):
        if r in relevant and r not in credited:
            dcg += 1.0 / math.log2(i + 1)
            credited.add(r)
    # Ideal DCG: all relevant docs at the top.
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate(retrieved_lists: list[list[str]], relevant_sets: list[set[str]], k: int = 5) -> dict:
    """Compute aggregate metrics over a set of queries."""
    assert len(retrieved_lists) == len(relevant_sets)
    n = len(retrieved_lists)
    if n == 0:
        return {f"recall@{k}": 0.0, "mrr": 0.0, f"ndcg@{k}": 0.0, "n": 0}

    recalls = [recall_at_k(r, rel, k) for r, rel in zip(retrieved_lists, relevant_sets)]
    rrs = [reciprocal_rank(r, rel) for r, rel in zip(retrieved_lists, relevant_sets)]
    ndcgs = [ndcg_at_k(r, rel, k) for r, rel in zip(retrieved_lists, relevant_sets)]

    return {
        f"recall@{k}": sum(recalls) / n,
        "mrr": sum(rrs) / n,
        f"ndcg@{k}": sum(ndcgs) / n,
        "n": n,
    }


# ---------------------------------------------------------------------
#  Retrieval runners
# ---------------------------------------------------------------------

def _build_dense_only_retriever(qdrant, collection):
    """A dense-only retriever that bypasses BM25/RRF (baseline)."""
    from app.services.retriever import RetrievedSource, SNIPPET_MAX_CHARS, _payload_str, _truncate
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    class DenseOnlyRetriever:
        def search(self, query_vector, query_text_segmented, department_id, top_k=8, prefetch_limit=50):
            results = qdrant.query_points(
                collection_name=collection,
                query=query_vector,
                using="dense",
                query_filter=Filter(must=[FieldCondition(
                    key="department_id", match=MatchValue(value=department_id)
                )]),
                limit=top_k,
                with_payload=True,
            )
            return [
                RetrievedSource(
                    id=str(p.id),
                    document_id=_payload_str(p.payload, "document_id", ""),
                    title=_payload_str(p.payload, "original_name", ""),
                    text=_payload_str(p.payload, "text", ""),
                    snippet=_truncate(_payload_str(p.payload, "text", ""), SNIPPET_MAX_CHARS),
                    score=float(p.score),
                )
                for p in results.points
            ]
    return DenseOnlyRetriever()


def run_evaluation(department_id: str = "EVAL", top_k: int = 5):
    """Run the full eval comparison: dense vs hybrid vs hybrid+reranker."""
    from app.config import get_settings
    from app.services.embedder import Embedder
    from app.services.retriever import Retriever
    from app.services.segmenter import segment
    from qdrant_client import QdrantClient

    settings = get_settings()
    print(f"Connecting to Qdrant: {settings.qdrant_url}, collection: {settings.collection_name}")
    print(f"Eval queries: {len(EVAL_QUERIES)}, top_k: {top_k}, department: {department_id}")
    print("=" * 70)

    qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    embedder = Embedder(
        settings.embed_model,
        max_length=settings.embed_max_length,
        embed_batch_size=settings.embed_batch_size,
        torch_threads=settings.embed_torch_threads,
    )
    hybrid_retriever = Retriever(qdrant, settings.collection_name, rrf_k=settings.hybrid_rrf_k)
    dense_retriever = _build_dense_only_retriever(qdrant, settings.collection_name)

    relevant_sets = [rel for _, rel in EVAL_QUERIES]

    # ---- Embed all queries once ----
    queries = [q for q, _ in EVAL_QUERIES]
    print("Embedding queries...")
    query_vecs = embedder.encode(queries)
    query_segs = [segment(q) for q in queries]

    # ---- Config 1: Dense only (baseline) ----
    print("\n[1/3] Dense-only (baseline)...")
    dense_results = []
    for i, q in enumerate(queries):
        sources = dense_retriever.search(
            query_vector=query_vecs[i], query_text_segmented=query_segs[i],
            department_id=department_id, top_k=top_k,
        )
        dense_results.append([s.title for s in sources])
        print(f"  Q{i+1}: {q[:50]}... -> {[s.title[:30] for s in sources[:3]]}")

    # ---- Config 2: Hybrid (RRF) ----
    print("\n[2/3] Hybrid (dense + BM25 RRF)...")
    hybrid_results = []
    for i, q in enumerate(queries):
        sources = hybrid_retriever.search(
            query_vector=query_vecs[i], query_text_segmented=query_segs[i],
            department_id=department_id, top_k=top_k,
        )
        hybrid_results.append([s.title for s in sources])

    # ---- Config 3: Hybrid + reranker ----
    print("\n[3/3] Hybrid + reranker...")
    rerank_results = []
    try:
        from app.services.reranker import Reranker
        reranker = Reranker(
            model_name=settings.reranker_model,
            ram_threshold_gb=settings.reranker_ram_threshold_gb,
        )
        for i, q in enumerate(queries):
            sources = hybrid_retriever.search(
                query_vector=query_vecs[i], query_text_segmented=query_segs[i],
                department_id=department_id, top_k=top_k,
            )
            # Rerank in place
            texts = [s.text for s in sources]
            ranked = reranker.rerank(q, texts, top_k=top_k)
            rerank_results.append([sources[idx].title for idx, _ in ranked])
    except Exception as e:  # noqa: BLE001
        print(f"  Reranker unavailable ({e}), skipping config 3.")
        rerank_results = hybrid_results

    # ---- Compute metrics ----
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Config':<25} {'recall@'+str(top_k):<12} {'MRR':<10} {'nDCG@'+str(top_k):<12}")
    print("-" * 70)

    for name, results in [
        ("Dense (baseline)", dense_results),
        ("Hybrid (RRF)", hybrid_results),
        ("Hybrid + Reranker", rerank_results),
    ]:
        m = evaluate(results, relevant_sets, k=top_k)
        print(f"{name:<25} {m[f'recall@{top_k}']:<12.3f} {m['mrr']:<10.3f} {m[f'ndcg@{top_k}']:<12.3f}")

    qdrant.close()
    print("\nDone.")


if __name__ == "__main__":
    dept = sys.argv[1] if len(sys.argv) > 1 else "EVAL"
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    run_evaluation(department_id=dept, top_k=k)
