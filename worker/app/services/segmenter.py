"""Vietnamese word segmentation for BM25 indexing.

The critical invariant: the SAME segmenter MUST run at both ingest-time
(and stored in ``text_segmented`` payload) and query-time (to segment the
query string before BM25 lookup). Asymmetric segmentation silently destroys
keyword precision — e.g. indexing "khách hàng" as raw whitespace tokens but
querying with the segmented form "khách_hàng" (or vice versa) produces zero
BM25 overlap on compound words.

Uses ``pyvi.ViTokenizer`` (CRF-based, ~MB footprint, CPU-only). Inserts
underscores between compound-word syllables: ``"khách hàng"`` → ``"khách_hàng"``.
Qdrant's default whitespace tokenizer then treats each compound as one token.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_segmenter = None  # lazily initialised singleton


def _get_segmenter():
    """Lazy-initialise the pyvi tokenizer (first call loads the CRF model)."""
    global _segmenter
    if _segmenter is None:
        from pyvi import ViTokenizer
        _segmenter = ViTokenizer
        logger.info("vietnamese_segmenter_ready engine=pyvi")
    return _segmenter


def segment(text: str) -> str:
    """Segment Vietnamese text for BM25-compatible tokenization.

    Compound words are joined with underscores so a subsequent whitespace
    split treats them as single tokens.

    Idempotent: already-segmented text (containing underscores) passes
    through without double-segmenting (pyvi handles this correctly).

    Returns the segmented string, or the original text unchanged on any
    failure (segmentation is best-effort — dense search still works).
    """
    if not text:
        return text
    try:
        tok = _get_segmenter()
        return tok.tokenize(text)
    except Exception:  # noqa: BLE001 — segmentation must never break the pipeline
        logger.warning("segmentation_failed len=%d — returning raw text", len(text))
        return text
