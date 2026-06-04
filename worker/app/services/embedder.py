"""Singleton bge-m3 embedder.

Loaded **once** at FastAPI lifespan startup — the model is ~2 GB and
takes ~30 s to instantiate, so we cannot do it per request.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Embedder:
    """Wraps LlamaIndex's HuggingFaceEmbedding for ``BAAI/bge-m3``."""

    def __init__(self, model_name: str):
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding

        logger.info("loading_embedder_model model=%s (this can take a while)", model_name)
        # `trust_remote_code=False` because bge-m3 ships pure transformers code.
        self._model = HuggingFaceEmbedding(model_name=model_name)
        logger.info("embedder_ready")

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts → list of dense vectors (cosine-normalised).

        Empty input returns an empty list (no model call).
        """
        if not texts:
            return []
        return self._model.get_text_embedding_batch(texts, show_progress=False)
