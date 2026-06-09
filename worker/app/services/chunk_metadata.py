"""Chunk text enrichment before embedding/storage.

Every chunk gets explicit document-level context so isolated table/list rows
still carry the source title/topic into vector search and prompt context.
"""

from __future__ import annotations

from pathlib import Path


def infer_document_topic(original_name: str) -> str:
    """Best-effort topic from filename when no richer document topic exists."""
    stem = Path(original_name).stem.strip()
    return stem or original_name.strip() or "Tài liệu nội bộ"


def prepend_document_context(text: str, original_name: str, document_topic: str | None = None) -> str:
    """Prefix one chunk with document metadata used by embeddings + retrieval."""
    topic = (document_topic or infer_document_topic(original_name)).strip()
    title = original_name.strip() or "Tài liệu nội bộ"

    return (
        f"Document Name: {title}\n"
        f"Document Topic: {topic}\n\n"
        f"{text.strip()}"
    )


def prepend_document_context_to_chunks(
    chunks: list[str],
    original_name: str,
    document_topic: str | None = None,
) -> list[str]:
    """Prefix all chunks with document metadata."""
    return [
        prepend_document_context(chunk, original_name, document_topic)
        for chunk in chunks
    ]