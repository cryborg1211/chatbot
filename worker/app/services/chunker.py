"""Splits Documents into chunks (LlamaIndex TextNodes) for embedding.

Strategy:
  - Docling documents  -> Markdown heading-based split (preserves tables)
  - Plain-text docs    -> SentenceSplitter    (token-window fallback)

Table safety: Lines containing consecutive | characters (markdown table
rows) are detected and kept together as atomic blocks that are never
split mid-table.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_TABLE_LINE_RE = re.compile(r"^\s*\|")

# Historical word-based default; the real token cap is always passed from main.py.
_DEFAULT_TOKEN_CAP = 1024


class Chunker:
    """Heading-based chunker for Docling, SentenceSplitter fallback for plain text.

    Coarse structural splits (heading recursion) stay word-based for cheapness;
    the two places that decide FINAL chunk boundaries — table row-splitting and
    the final-emission safety check — use exact bge-m3 token counts so no chunk
    silently exceeds the embedder's real token cap (``token_cap``).

        Chunker(chunk_size=1024, chunk_overlap=250, token_cap=1024)
    """

    def __init__(
        self,
        chunk_size: int,
        chunk_overlap: int,
        token_cap: int = _DEFAULT_TOKEN_CAP,
    ):
        from llama_index.core.node_parser import SentenceSplitter
        from transformers import AutoTokenizer

        self._fallback = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._chunk_size = chunk_size
        self._token_cap = token_cap
        # Chunker owns its own tokenizer (a few-MB SentencePiece config download,
        # NOT the 2GB model weights the Embedder loads) — separate concern.
        self._tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
        logger.info("chunker_tokenizer_ready model=BAAI/bge-m3 token_cap=%d", token_cap)

    def _count_tokens(self, text: str) -> int:
        """Exact bge-m3 token count (no special tokens — matches embed content)."""
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def split(self, documents: list[Any]) -> list[Any]:
        """Returns a flat list of TextNodes, ready to embed.

        Accepts a mixed list of ``DoclingResult`` (from Docling-parsed
        documents) and plain LlamaIndex ``Document`` objects (from the
        text/plain path).
        """
        all_nodes: list[Any] = []

        for doc in documents:
            if hasattr(doc, "docling_doc"):
                md_text = doc.text if hasattr(doc, "text") else ""
                metadata = doc.metadata if hasattr(doc, "metadata") else {}
                nodes = _split_markdown_by_headings(
                    md_text,
                    self._chunk_size,
                    self._fallback,
                    metadata,
                    token_counter=self._count_tokens,
                    token_cap=self._token_cap,
                )
                all_nodes.extend(nodes)
            else:
                nodes = self._fallback.get_nodes_from_documents([doc])
                all_nodes.extend(nodes)

        all_nodes = _filter_noise_chunks(all_nodes)
        all_nodes = _merge_small_chunks(all_nodes, min_words=30)
        # Final-emission safety check: exact-count every emitted node; re-split any
        # that still exceed the real token cap (Decision A/C — re-split, never
        # truncate). Catches merge artifacts and dense numeric/table content that
        # slipped past the coarse word-based upstream splits.
        all_nodes = self._enforce_token_cap(all_nodes)
        return all_nodes

    def _enforce_token_cap(self, nodes: list[Any]) -> list[Any]:
        """Guarantee every emitted node is <= ``self._token_cap`` exact tokens.

        Decision A/C — re-split, never truncate. Strategy per oversized node:
          1. line/row-boundary split (``_split_table_block``);
          2. if that didn't reduce it, SentenceSplitter fallback;
          3. any residual still over cap → hard token-window split (guaranteed
             to fit at any cap; still loses nothing — all tokens are re-emitted
             across adjacent windows).
        """
        result: list[Any] = []
        for node in nodes:
            content = node.get_content()
            if self._count_tokens(content) <= self._token_cap:
                result.append(node)
                continue
            metadata = node.metadata if hasattr(node, "metadata") else {}
            logger.warning(
                "chunk_token_overflow_resplit tokens=%d cap=%d",
                self._count_tokens(content), self._token_cap,
            )
            for piece in self._resplit_oversized(content, metadata):
                result.append(piece)
        return result

    def _resplit_oversized(self, content: str, metadata: dict[str, Any]) -> list[Any]:
        """Return nodes for one oversized chunk, each guaranteed <= token_cap."""
        from llama_index.core import Document

        # 1. Line/row-boundary split.
        pieces = _split_table_block(
            content.split("\n"),
            self._token_cap,
            metadata,
            token_counter=self._count_tokens,
        )
        # 2. If line-splitting didn't actually reduce it, try SentenceSplitter.
        if len(pieces) <= 1:
            doc = Document(text=content, metadata=metadata)
            pieces = self._fallback.get_nodes_from_documents([doc])

        # 3. Backstop: any piece still over cap (e.g. header+single wide row, or an
        #    unbreakable line) gets a hard token-window split that always fits.
        final: list[Any] = []
        for piece in pieces:
            piece_text = piece.get_content()
            if self._count_tokens(piece_text) <= self._token_cap:
                final.append(piece)
            else:
                final.extend(self._hard_token_split(piece_text, metadata))
        return final

    def _hard_token_split(self, text: str, metadata: dict[str, Any]) -> list[Any]:
        """Last-resort split by exact token windows — never exceeds the cap.

        Slices the token-id stream into ``token_cap``-sized windows and decodes
        each back to text. No content is dropped (every token lands in exactly
        one window), so this bounds size without the silent truncation this whole
        fix exists to prevent.
        """
        from llama_index.core.schema import TextNode

        ids = self._tokenizer.encode(text, add_special_tokens=False)
        cap = max(1, self._token_cap)
        nodes: list[Any] = []
        for i in range(0, len(ids), cap):
            window = ids[i : i + cap]
            piece = self._tokenizer.decode(window, skip_special_tokens=True).strip()
            if piece:
                nodes.append(TextNode(text=piece, metadata=metadata))
        return nodes or [TextNode(text=text, metadata=metadata)]


# --------------- Heading-based markdown splitting ---------------


def _split_markdown_by_headings(
    text: str,
    chunk_size: int,
    fallback_splitter: Any,
    metadata: dict[str, Any],
    token_counter: Callable[[str], int],
    token_cap: int,
) -> list[Any]:
    """Split raw markdown at ``##`` heading boundaries, keeping tables intact."""
    from llama_index.core.schema import TextNode

    sections = _heading_split(text, level=2)
    result_nodes: list[Any] = []

    for section_text in sections:
        section_text = section_text.strip()
        if not section_text:
            continue
        if len(section_text.split()) <= chunk_size:
            result_nodes.append(TextNode(text=section_text, metadata=metadata))
        else:
            sub_nodes = _recursive_split(
                section_text,
                chunk_size,
                fallback_splitter,
                metadata,
                token_counter,
                token_cap,
                heading_level=3,
            )
            result_nodes.extend(sub_nodes)

    return result_nodes


def _heading_split(text: str, level: int = 2) -> list[str]:
    """Split *text* at markdown headings of exactly *level*.

    ``level=2`` splits at ``## `` lines (but not ``### ``).
    Content before the first heading becomes its own section.
    """
    prefix = "#" * level
    next_prefix = "#" * (level + 1)
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        is_heading = line.startswith(prefix + " ") and not line.startswith(
            next_prefix
        )
        if is_heading and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append("\n".join(current))

    return sections


def _recursive_split(
    text: str,
    chunk_size: int,
    fallback_splitter: Any,
    metadata: dict[str, Any],
    token_counter: Callable[[str], int],
    token_cap: int,
    heading_level: int = 3,
) -> list[Any]:
    """Recursively split an oversized section, trying deeper headings first."""
    from llama_index.core.schema import TextNode

    if heading_level <= 6:
        sub_sections = _heading_split(text, level=heading_level)
        if len(sub_sections) > 1:
            result: list[Any] = []
            for sub in sub_sections:
                sub = sub.strip()
                if not sub:
                    continue
                if len(sub.split()) <= chunk_size:
                    result.append(TextNode(text=sub, metadata=metadata))
                else:
                    result.extend(
                        _recursive_split(
                            sub,
                            chunk_size,
                            fallback_splitter,
                            metadata,
                            token_counter,
                            token_cap,
                            heading_level=heading_level + 1,
                        )
                    )
            return result

    blocks = _extract_atomic_blocks(text)
    return _group_blocks(
        blocks, chunk_size, fallback_splitter, metadata, token_counter, token_cap
    )


# --------------- Atomic block extraction ---------------


def _extract_atomic_blocks(text: str) -> list[tuple[str, str]]:
    """Split *text* into ``('table', ...)`` and ``('prose', ...)`` blocks.

    Table blocks (contiguous lines matching ``_TABLE_LINE_RE``) are atomic —
    they are never split.  Prose is split at blank-line paragraph boundaries.
    """
    lines = text.split("\n")
    blocks: list[tuple[str, str]] = []
    current_lines: list[str] = []
    in_table = False

    def _flush(block_type: str) -> None:
        content = "\n".join(current_lines).strip()
        if content:
            blocks.append((block_type, content))

    for line in lines:
        is_table_line = bool(_TABLE_LINE_RE.match(line))

        if is_table_line:
            if not in_table and current_lines:
                _flush("prose")
                current_lines.clear()
            in_table = True
            current_lines.append(line)
        else:
            if in_table:
                _flush("table")
                current_lines.clear()
                in_table = False

            if not line.strip() and current_lines:
                _flush("prose")
                current_lines.clear()
            elif line.strip():
                current_lines.append(line)

    if current_lines:
        _flush("table" if in_table else "prose")

    return blocks


def _group_blocks(
    blocks: list[tuple[str, str]],
    chunk_size: int,
    fallback_splitter: Any,
    metadata: dict[str, Any],
    token_counter: Callable[[str], int],
    token_cap: int,
) -> list[Any]:
    """Greedily pack atomic blocks into chunks that fit *chunk_size*.

    Oversized table blocks -> ``_split_table_block`` (row-boundary split, bounded
    by the exact ``token_cap``). Oversized prose blocks -> ``fallback_splitter``.
    """
    from llama_index.core import Document
    from llama_index.core.schema import TextNode

    result: list[Any] = []
    current_parts: list[str] = []
    current_words = 0

    def _flush_current() -> None:
        nonlocal current_words
        if current_parts:
            combined = "\n\n".join(current_parts).strip()
            if combined:
                result.append(TextNode(text=combined, metadata=metadata))
            current_parts.clear()
            current_words = 0

    for block_type, block_text in blocks:
        block_words = len(block_text.split())

        if block_words > chunk_size:
            _flush_current()
            if block_type == "table":
                table_lines = block_text.split("\n")
                # Table splitting uses the EXACT token cap, not the word budget.
                result.extend(
                    _split_table_block(
                        table_lines, token_cap, metadata, token_counter=token_counter
                    )
                )
            else:
                doc = Document(text=block_text, metadata=metadata)
                result.extend(
                    fallback_splitter.get_nodes_from_documents([doc])
                )
        elif current_words + block_words > chunk_size and current_parts:
            _flush_current()
            current_parts.append(block_text)
            current_words = block_words
        else:
            current_parts.append(block_text)
            current_words += block_words

    _flush_current()
    return result


# --------------- Table row-boundary splitting ---------------


def _split_table_block(
    lines: list[str],
    token_budget: int,
    metadata: dict[str, Any],
    token_counter: Callable[[str], int],
) -> list[Any]:
    """Split a contiguous ``|``-prefixed table block at row boundaries.

    Keeps header + separator prepended to every sub-chunk. ``token_budget`` is
    an EXACT token cap (bge-m3), counted via ``token_counter`` — not a word count
    — so no emitted sub-chunk exceeds the embedder's real token limit.

    Also reused as a generic line-boundary splitter by the final-emission safety
    check: when there is no ``|``-table header it falls through to per-line
    packing, which still bounds output by the token budget.
    """
    from llama_index.core.schema import TextNode

    _SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

    if not lines:
        return []

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
        if header_row is not None and separator_row is None:
            if not _SEP_RE.match(stripped):
                continue

    if separator_row is None:
        # No markdown table header — treat every line as a data row with an empty
        # prefix so the final-emission safety check can still split oversized
        # non-table blocks at line boundaries (Decision C).
        return _pack_rows_by_tokens("", lines, token_budget, metadata, token_counter)

    data_rows = lines[data_start_idx:]
    if not data_rows:
        full_text = "\n".join(lines)
        return [TextNode(text=full_text, metadata=metadata)]

    prefix = header_row + "\n" + separator_row
    return _pack_rows_by_tokens(prefix, data_rows, token_budget, metadata, token_counter)


def _pack_rows_by_tokens(
    prefix: str,
    rows: list[str],
    token_budget: int,
    metadata: dict[str, Any],
    token_counter: Callable[[str], int],
) -> list[Any]:
    """Greedily pack ``rows`` into chunks bounded by ``token_budget`` (exact tokens).

    Each chunk is ``prefix + "\\n" + rows...`` (prefix may be empty). A single row
    that alone exceeds the budget is emitted on its own — line boundaries are the
    finest granularity available here; the SentenceSplitter fallback in
    ``_enforce_token_cap`` handles the pathological unbreakable-line case.
    """
    from llama_index.core.schema import TextNode

    prefix_tokens = token_counter(prefix) if prefix else 0

    def _assemble(chunk_rows: list[str]) -> str:
        body = "\n".join(chunk_rows)
        return (prefix + "\n" + body) if prefix else body

    result_nodes: list[Any] = []
    current_rows: list[str] = []
    current_token_count = prefix_tokens

    for row in rows:
        row_tokens = token_counter(row)
        if current_rows and (current_token_count + row_tokens) > token_budget:
            result_nodes.append(TextNode(text=_assemble(current_rows), metadata=metadata))
            current_rows = [row]
            current_token_count = prefix_tokens + row_tokens
        else:
            current_rows.append(row)
            current_token_count += row_tokens

    if current_rows:
        result_nodes.append(TextNode(text=_assemble(current_rows), metadata=metadata))

    return result_nodes


# --------------- Noise filtering and small chunk merging ---------------

_NOISE_PATTERNS = re.compile(
    r"^("
    r"KT\.\s*GIÁM\s*ĐỐC|PHÓ\s*GIÁM\s*ĐỐC|GIÁM\s*ĐỐC|"
    r"NGƯỜI\s*LẬP\s*BIỂU|NGƯỜI\s*LẬP|"
    r"PHỤ\s*LỤC\s*\d*|"
    r"Nơi\s*nhận|N\s*ơ\s*i\s*nh\s*ậ\s*n|"
    r"Đơn\s*vị\s*tính"
    r")$",
    re.IGNORECASE,
)

_SIGNATURE_RE = re.compile(
    r"^[A-ZÀ-Ỹ][a-zà-ỹ]+(\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){1,5}$"
)

# Signature/role vocabulary. A short chunk is only treated as a signature block
# when it ALSO contains one of these — so ordinary Vietnamese Titlecase headings
# and proper nouns ("Ủy Ban Nhân Dân") that carry no role word survive.
_TITLE_WORD_RE = re.compile(
    r"GIÁM\s*ĐỐC|TRƯỞNG|PHÓ|CHỦ\s*TỊCH|BÍ\s*THƯ|KT\.", re.IGNORECASE
)


def _is_noise(text: str) -> bool:
    """True for chunks that are just signatures, labels, or page decorations."""
    stripped = text.strip()
    if not stripped:
        return True
    words = len(stripped.split())
    if words > 15:
        return False
    if _NOISE_PATTERNS.match(stripped):
        logger.debug("noise_filtered_pattern text=%r", stripped)
        return True
    # Signature block: a short chunk that contains a recognisable role/title word.
    # Requiring the title word (instead of bare Titlecase shape) is the HIGH#1 fix —
    # it stops ordinary proper nouns from being silently deleted while still
    # catching real signature blocks like "KT. GIÁM ĐỐC\nNguyễn Văn A".
    if words <= 6 and _TITLE_WORD_RE.search(stripped):
        logger.debug("noise_filtered_signature text=%r", stripped)
        return True
    # Symbol-soup check. Do NOT strip digits (\d removed) so legitimate short
    # reference numbers like "12/QĐ" count their digits toward real content and
    # survive (debt #9 fix).
    clean = re.sub(r"[,.\-–—;:()=\s]", "", stripped)
    if len(clean) < 3:
        logger.debug("noise_filtered_symbol_soup text=%r", stripped)
        return True
    return False


def _filter_noise_chunks(nodes: list[Any]) -> list[Any]:
    return [n for n in nodes if not _is_noise(n.get_content())]


def _merge_small_chunks(nodes: list[Any], min_words: int = 30) -> list[Any]:
    """Merge consecutive tiny chunks into their nearest neighbor."""
    if len(nodes) <= 1:
        return nodes

    from llama_index.core.schema import TextNode

    merged: list[Any] = []

    for node in nodes:
        text = node.get_content()
        words = len(text.split())

        if words >= min_words or not merged:
            merged.append(node)
        else:
            prev = merged[-1]
            prev_text = prev.get_content()
            combined = prev_text.rstrip() + "\n\n" + text.lstrip()
            merged[-1] = TextNode(
                text=combined,
                metadata=prev.metadata if hasattr(prev, "metadata") else {},
            )

    return merged
