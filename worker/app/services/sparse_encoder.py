"""Real server-side BM25 sparse vector encoding.

Qdrant's payload TEXT index (``PayloadSchemaType.TEXT``) is FILTER-ONLY —
it supports boolean ``MatchText`` conditions but CANNOT be a scored/ranked
branch inside ``query_points``/``Prefetch``. There is no query type in
Qdrant's Query API for "match this payload text field and rank by BM25".

Genuine server-side BM25 requires a named SPARSE vector plus
``Modifier.IDF`` on the collection (Qdrant's documented hybrid-search
pattern: https://qdrant.tech/documentation/concepts/hybrid-queries).
``fastembed``'s ``Qdrant/bm25`` model produces the raw term-frequency
sparse vector (hashed tokens -> counts); Qdrant applies IDF weighting
server-side at query time using corpus statistics, completing the BM25
score.

The critical invariant carries over from the old text-index design: the
SAME segmenter (``segmenter.segment``) must run before encoding, at both
ingest and query time, so compound-word tokens match between corpus and
query.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_model = None  # lazily initialised singleton

SPARSE_VECTOR_NAME = "bm25"
BM25_MODEL_NAME = "Qdrant/bm25"


def _get_model():
    global _model
    if _model is None:
        from fastembed import SparseTextEmbedding
        _model = SparseTextEmbedding(model_name=BM25_MODEL_NAME)
        logger.info("sparse_bm25_encoder_ready model=%s", BM25_MODEL_NAME)
    return _model


class SparseVectorResult:
    """Plain (indices, values) pair — mirrors qdrant_client.models.SparseVector
    shape without importing it here, so this module has no Qdrant dependency."""

    __slots__ = ("indices", "values")

    def __init__(self, indices: list[int], values: list[float]):
        self.indices = indices
        self.values = values


def encode(texts: list[str]) -> list[SparseVectorResult]:
    """Encode already-segmented text into BM25 sparse vectors.

    Args:
        texts: Pre-segmented text (output of ``segmenter.segment()``), one
            per chunk or query. Segmentation MUST happen before this call —
            this function does not segment.

    Returns:
        One ``SparseVectorResult`` per input text, in the same order.
        Empty input text produces an empty sparse vector (no crash).
    """
    if not texts:
        return []
    model = _get_model()
    embeddings = list(model.embed(texts))
    return [
        SparseVectorResult(indices=list(e.indices), values=list(e.values))
        for e in embeddings
    ]


def encode_one(text: str) -> SparseVectorResult:
    """Convenience wrapper for encoding a single (already-segmented) query string."""
    return encode([text])[0]
