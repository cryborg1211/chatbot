"""Splits Documents into chunks (LlamaIndex TextNodes) for embedding."""

from __future__ import annotations

from typing import Any


class Chunker:
    """Thin wrapper around LlamaIndex's :class:`SentenceSplitter`.

    Defaults (512 tokens, 50 token overlap) tuned for bge-m3:
      - 512 ≈ a paragraph or two — small enough for tight context, big
        enough to keep a single thought together.
      - 50 overlap lets a sentence that straddles two chunks survive.
    """

    def __init__(self, chunk_size: int, chunk_overlap: int):
        from llama_index.core.node_parser import SentenceSplitter

        self._splitter = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def split(self, documents: list[Any]) -> list[Any]:
        """Returns a flat list of TextNodes, ready to embed."""
        return self._splitter.get_nodes_from_documents(documents)
