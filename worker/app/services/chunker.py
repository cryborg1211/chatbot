"""Splits Documents into chunks (LlamaIndex TextNodes) for embedding.

Hybrid strategy:
  - Docling documents  -> HierarchicalChunker (structure-aware)
  - Plain-text docs    -> SentenceSplitter    (token-window fallback)

Oversized chunks from HierarchicalChunker are post-processed by a
Markdown-aware splitter that keeps table headers attached to every
continuation chunk.
"""

from __future__ import annotations

import re
from typing import Any


class Chunker:
    """Hybrid chunker: HierarchicalChunker primary, SentenceSplitter fallback.

    Constructor signature is unchanged from the original single-strategy
    implementation so ``main.py`` needs no edits::

        Chunker(chunk_size=1024, chunk_overlap=250)
    """

    def __init__(self, chunk_size: int, chunk_overlap: int):
        from docling.chunking import HierarchicalChunker
        from llama_index.core.node_parser import SentenceSplitter

        self._hier = HierarchicalChunker(max_tokens=chunk_size)
        self._fallback = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, documents: list[Any]) -> list[Any]:
        """Returns a flat list of TextNodes, ready to embed.

        Accepts a mixed list of ``DoclingResult`` (from Docling-parsed
        documents) and plain LlamaIndex ``Document`` objects (from the
        text/plain path).
        """
        from llama_index.core.schema import TextNode

        all_nodes: list[Any] = []

        for doc in documents:
            # DoclingResult carries .docling_doc for structure-aware chunking
            if hasattr(doc, "docling_doc"):
                chunks = list(self._hier.chunk(dl_doc=doc.docling_doc))
                for chunk in chunks:
                    node = TextNode(
                        text=chunk.text,
                        metadata=doc.metadata,
                    )
                    all_nodes.append(node)
            else:
                # Plain LlamaIndex Document -> SentenceSplitter
                nodes = self._fallback.get_nodes_from_documents([doc])
                all_nodes.extend(nodes)

        # Post-process: split any oversized chunks at table-row boundaries
        all_nodes = _split_oversized_chunks(
            all_nodes, self._chunk_size, self._fallback
        )
        return all_nodes


# ---------------------------------------------------------------------
#  Post-processing helpers (module-private)
# ---------------------------------------------------------------------

def _split_oversized_chunks(
    nodes: list[Any],
    chunk_size: int,
    fallback_splitter: Any,
) -> list[Any]:
    """Re-split any node whose word count exceeds *chunk_size*.

    Token counting uses ``len(text.split())`` as a conservative
    approximation (plan specifies this is intentional, not BPE).
    """
    result: list[Any] = []
    for node in nodes:
        text = node.get_content()
        if len(text.split()) > chunk_size:
            metadata = node.metadata if hasattr(node, "metadata") else {}
            sub_nodes = _split_markdown_chunk(
                text, chunk_size, fallback_splitter, metadata
            )
            result.extend(sub_nodes)
        else:
            result.append(node)
    return result


def _split_markdown_chunk(
    text: str,
    chunk_size: int,
    fallback_splitter: Any,
    metadata: dict[str, Any],
) -> list[Any]:
    """Split oversized Markdown text at table-row boundaries.

    Algorithm:
      1. Walk lines and identify contiguous ``|``-prefixed table blocks.
      2. Within a table block: first ``|`` line = header, second line
         matching ``|---|`` pattern = separator.
      3. Group data rows into sub-chunks that fit within *chunk_size*
         tokens.  Each sub-chunk gets the header + separator prepended.
      4. Non-table prose exceeding *chunk_size* is delegated to
         ``fallback_splitter``.
      5. Returns a flat ``list[TextNode]``.
    """
    from llama_index.core import Document
    from llama_index.core.schema import TextNode

    _SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

    lines = text.split("\n")
    sections: list[dict[str, Any]] = []  # {"type": "table"|"prose", "lines": [...]}

    # ---- Group lines into table blocks and prose blocks ----
    current_type: str | None = None
    current_lines: list[str] = []

    for line in lines:
        is_table_line = line.strip().startswith("|")
        line_type = "table" if is_table_line else "prose"

        if line_type != current_type:
            if current_lines:
                sections.append({"type": current_type, "lines": current_lines})
            current_type = line_type
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({"type": current_type, "lines": current_lines})

    # ---- Process each section ----
    result_nodes: list[Any] = []

    for section in sections:
        if section["type"] == "table":
            table_nodes = _split_table_block(
                section["lines"], chunk_size, metadata
            )
            result_nodes.extend(table_nodes)
        else:
            # Prose section
            prose_text = "\n".join(section["lines"])
            if len(prose_text.split()) > chunk_size:
                # Delegate oversized prose to SentenceSplitter
                doc = Document(text=prose_text, metadata=metadata)
                sub_nodes = fallback_splitter.get_nodes_from_documents([doc])
                result_nodes.extend(sub_nodes)
            else:
                if prose_text.strip():
                    result_nodes.append(
                        TextNode(text=prose_text, metadata=metadata)
                    )

    return result_nodes


def _split_table_block(
    lines: list[str],
    chunk_size: int,
    metadata: dict[str, Any],
) -> list[Any]:
    """Split a contiguous block of ``|``-prefixed table lines.

    Keeps the header row + separator row attached to every sub-chunk.
    """
    from llama_index.core.schema import TextNode

    _SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

    if not lines:
        return []

    # Identify header and separator
    header_row: str | None = None
    separator_row: str | None = None
    data_start_idx = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if header_row is None and stripped.startswith("|"):
            header_row = line
            continue
        if separator_row is None and _SEP_RE.match(stripped):
            separator_row = line
            data_start_idx = i + 1
            break
        # If second line is not a separator, treat whole block as data
        # with the first line as header (no separator)
        if header_row is not None and separator_row is None:
            if not _SEP_RE.match(stripped):
                # First line is header, no separator detected yet,
                # keep scanning
                continue

    # If no separator found, treat entire block as one chunk
    if separator_row is None:
        full_text = "\n".join(lines)
        return [TextNode(text=full_text, metadata=metadata)]

    data_rows = lines[data_start_idx:]

    if not data_rows:
        # Table with header + separator but no data rows
        full_text = "\n".join(lines)
        return [TextNode(text=full_text, metadata=metadata)]

    # Group data rows into sub-chunks fitting within chunk_size
    prefix = header_row + "\n" + separator_row
    prefix_tokens = len(prefix.split())

    result_nodes: list[Any] = []
    current_rows: list[str] = []
    current_token_count = prefix_tokens

    for row in data_rows:
        row_tokens = len(row.split())
        if current_rows and (current_token_count + row_tokens) > chunk_size:
            # Flush current sub-chunk
            chunk_text = prefix + "\n" + "\n".join(current_rows)
            result_nodes.append(TextNode(text=chunk_text, metadata=metadata))
            current_rows = [row]
            current_token_count = prefix_tokens + row_tokens
        else:
            current_rows.append(row)
            current_token_count += row_tokens

    # Flush remaining rows
    if current_rows:
        chunk_text = prefix + "\n" + "\n".join(current_rows)
        result_nodes.append(TextNode(text=chunk_text, metadata=metadata))

    return result_nodes
