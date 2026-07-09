"""Singleton bge-m3 embedder.

Loaded **once** at FastAPI lifespan startup — the model is ~2 GB and
takes ~30 s to instantiate, so we cannot do it per request.

Memory safety:
  - ``max_length`` is capped (default 1024) instead of bge-m3's native 8192.
    Attention is O(seq^2); 8192 on CPU spikes multiple GB and can OOM a laptop.
  - ``embed_batch_size`` is small (default 4) so few sequences are resident.
  - torch CPU threads are capped to stop oversubscription ballooning memory.
  - encoding is chunked + ``gc.collect()`` between sub-batches to release
    intermediate activation tensors promptly.
"""

from __future__ import annotations

import gc
import logging

logger = logging.getLogger(__name__)


class Embedder:
    """Wraps LlamaIndex's HuggingFaceEmbedding for ``BAAI/bge-m3``."""

    def __init__(
        self,
        model_name: str,
        max_length: int = 1024,
        embed_batch_size: int = 4,
        torch_threads: int = 2,
    ):
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        if torch_threads and torch_threads > 0:
            try:
                import torch
                torch.set_num_threads(torch_threads)
                logger.info("torch_threads_capped n=%d", torch_threads)
            except Exception:  # noqa: BLE001
                logger.warning("torch_thread_cap_failed — using default")

        self._batch_size = embed_batch_size

        logger.info(
            "loading_embedder_model model=%s max_length=%d batch=%d (this can take a while)",
            model_name, max_length, embed_batch_size,
        )
        # `trust_remote_code=False` because bge-m3 ships pure transformers code.
        # device="cpu" keeps it off any GPU that can't fit it; max_length cap is
        # the single biggest RAM lever here.
        self._model = HuggingFaceEmbedding(
            model_name=model_name,
            max_length=max_length,
            embed_batch_size=embed_batch_size,
            device="cpu",
        )
        logger.info("embedder_ready")

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Embed texts → list of dense vectors (cosine-normalised).

        Processes in small sub-batches with a GC between each so peak memory
        stays bounded regardless of how many chunks a document produced.
        Empty input returns an empty list (no model call).
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        step = max(1, self._batch_size)
        for i in range(0, len(texts), step):
            sub = texts[i : i + step]
            vectors.extend(
                self._model.get_text_embedding_batch(sub, show_progress=False)
            )
            gc.collect()
        return vectors
