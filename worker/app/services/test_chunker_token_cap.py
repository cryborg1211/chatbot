"""Fork 1 — token-cap enforcement regression test.

Constructs a real ``Chunker`` with a small ``token_cap`` and feeds a synthetic
markdown table whose rows exceed that cap combined. Asserts every emitted
``TextNode`` has an exact bge-m3 token count <= token_cap (Decision A/B: table
chunks never silently exceed the embedder token cap; overflow is re-split, not
truncated).

Hybrid tier: needs the bge-m3 tokenizer (a few-MB SentencePiece config, cached
after first download — NOT the 2GB model weights). If the tokenizer cannot be
fetched offline the whole module is skipped with a clear reason (never a fake
green).
"""

from __future__ import annotations

import pytest

# Skip the whole module cleanly if the tokenizer can't load (offline first run).
pytest.importorskip("transformers")

from app.services.loader import DoclingResult  # noqa: E402


@pytest.fixture(scope="module")
def small_cap_chunker():
    from app.services.chunker import Chunker

    try:
        # token_cap deliberately tiny so a modest table overflows and must split.
        return Chunker(chunk_size=1024, chunk_overlap=50, token_cap=40)
    except Exception as exc:  # noqa: BLE001 — offline tokenizer fetch failure
        pytest.skip(f"bge-m3 tokenizer unavailable (offline?): {exc}")


def _make_oversized_table_markdown(num_rows: int = 30) -> str:
    """A markdown table whose data rows together far exceed a 40-token cap."""
    header = "| Hạng mục | Số tiền | Ghi chú |"
    sep = "| --- | --- | --- |"
    rows = [
        f"| Khoản chi tiêu số {i} cho dự án | {i * 1000000} đồng | "
        f"Ghi chú chi tiết cho khoản mục thứ {i} năm 2024 |"
        for i in range(1, num_rows + 1)
    ]
    return "\n".join([header, sep, *rows])


def test_oversized_table_chunk_respects_token_cap(small_cap_chunker) -> None:
    chunker = small_cap_chunker
    md = _make_oversized_table_markdown()
    doc = DoclingResult(
        text=md,
        docling_doc=None,
        metadata={"source": "synthetic-table.md"},
    )

    nodes = chunker.split([doc])

    assert nodes, "chunker produced zero nodes for an oversized table"
    for node in nodes:
        tokens = chunker._count_tokens(node.get_content())
        assert tokens <= chunker._token_cap, (
            f"emitted node has {tokens} tokens, exceeds cap {chunker._token_cap}: "
            f"{node.get_content()!r}"
        )
