"""Cross-encoder reranker for Vietnamese RAG retrieval.

Rescores the RRF-fused top-N candidates using a cross-encoder model
(``bge-reranker-v2-m3``, multilingual / Vietnamese-strong). This is
the precision tier: BM25+dense gets you recall; the reranker gets you
the RIGHT top-k for the LLM.

Memory safety:
  - Lazy-loaded on first ``rerank()`` call (model is ~2 GB on disk).
  - RAM guard: skipped if available RAM is below ``ram_threshold_gb``.
  - Configurable on/off via Settings (``reranker_enabled``).
  - On any failure, falls back to returning the input list in original
    order (never blocks retrieval).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_model = None  # lazy-loaded singleton
_model_name: str | None = None


def _get_available_ram_gb() -> float:
    """Best-effort available RAM in GB. Returns inf if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return float("inf")


class Reranker:
    """Lazy-loaded, RAM-guarded cross-encoder reranker."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        ram_threshold_gb: float = 3.0,
    ):
        self._model_name = model_name
        self._ram_threshold_gb = ram_threshold_gb
        self._ready = False

    def _ensure_loaded(self) -> bool:
        """Load the model if not already loaded and RAM permits.

        Returns True if the model is ready, False if it was skipped.
        """
        global _model, _model_name
        if self._ready and _model is not None:
            return True

        ram = _get_available_ram_gb()
        if ram < self._ram_threshold_gb:
            logger.warning(
                "reranker_skipped_low_ram available=%.1fGB need=%.1fGB model=%s",
                ram, self._ram_threshold_gb, self._model_name,
            )
            return False

        try:
            from sentence_transformers import CrossEncoder
            logger.info(
                "reranker_loading model=%s (this can take a while)",
                self._model_name,
            )
            _model = CrossEncoder(self._model_name)
            _model_name = self._model_name
            self._ready = True
            logger.info("reranker_ready model=%s", self._model_name)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("reranker_load_failed model=%s", self._model_name)
            return False

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """Rescore (query, document) pairs via the cross-encoder.

        Args:
            query: The user query (raw Vietnamese, not segmented).
            documents: Full text of candidate chunks to rescore.
            top_k: If set, return only the top-k scored results.

        Returns:
            List of ``(original_index, score)`` tuples, sorted descending
            by score. The original_index refers to the input ``documents``
            list position.

        On any failure (model not loaded, OOM, etc.), returns the input
        order with uniform scores so retrieval proceeds unblocked.
        """
        if not documents:
            return []

        if not self._ensure_loaded():
            # Fallback: return in original order with neutral scores.
            return [(i, 1.0 / (i + 1)) for i in range(len(documents))]

        try:
            pairs = [(query, doc) for doc in documents]
            scores = _model.predict(pairs, show_progress_bar=False)

            # Build (index, score) pairs and sort by score descending.
            ranked = sorted(
                enumerate(float(s) for s in scores),
                key=lambda x: x[1],
                reverse=True,
            )
            if top_k is not None:
                ranked = ranked[:top_k]
            return ranked
        except Exception:  # noqa: BLE001
            logger.exception("reranker_predict_failed — returning original order")
            return [(i, 1.0 / (i + 1)) for i in range(len(documents))]
