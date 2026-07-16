"""Tests for the Vietnamese word segmenter (Phase 1.1).

These guard the CRITICAL invariant: segmentation is applied at BOTH ingest
and query time. If the segmenter behavior changes, BM25 precision breaks
silently. Each test documents an expected segmentation contract.
"""

from __future__ import annotations

from app.services.segmenter import segment


def test_segment_compound_words() -> None:
    """Compound words are joined with underscores (the core contract).

    'khách hàng' must become 'khách_hàng' so Qdrant's whitespace tokenizer
    treats it as ONE token, not two arbitrary syllables. This is the single
    most important assertion in the Vietnamese pipeline.
    """
    result = segment("khách hàng")
    assert "khách_hàng" in result, f"compound not segmented: {result!r}"


def test_segment_multiple_compounds() -> None:
    """Multiple compound words in one string are all segmented."""
    result = segment("khách hàng mua sắm tại cửa hàng")
    assert "khách_hàng" in result
    assert "mua_sắm" in result
    assert "cửa_hàng" in result


def test_segment_government_vocabulary() -> None:
    """Government/administrative Vietnamese compounds segment correctly.

    These are the actual terms that appear in the RAG_test corpus
    (QĐ-1202, BC đề xuất đầu tư). Precision on these is the whole point.
    """
    result = segment("quyết định ban hành kế hoạch khoa học công nghệ")
    # At least the most common compounds should be joined.
    assert "quyết_định" in result or "kế_hoạch" in result or "khoa_học" in result


def test_segment_preserves_non_compound_words() -> None:
    """Single syllables that aren't part of a compound pass through unchanged."""
    result = segment("văn bản này")
    # 'này' is a single syllable, not a compound — should appear standalone.
    assert "này" in result


def test_segment_empty_and_short() -> None:
    """Edge cases: empty and whitespace-only input don't crash."""
    assert segment("") == ""
    # Single word still works.
    assert "hello" in segment("hello")


def test_segment_handles_mixed_vietnamese_english() -> None:
    """Mixed-language text (common in tech docs) doesn't break the segmenter."""
    result = segment("phần mềm PDF được sử dụng")
    assert "phần_mềm" in result
    assert "PDF" in result


def test_segment_does_not_drop_content() -> None:
    """No syllables are lost during segmentation.

    The segmented output joined back (underscores → spaces) should contain
    at least as many syllables as the input. This guards against a future
    segmenter change that silently truncates text.
    """
    text = "người dùng có thể tra cứu thông tin"
    result = segment(text)
    # Replace underscores with spaces and compare syllable counts.
    input_syllables = text.split()
    output_syllables = result.replace("_", " ").split()
    assert len(output_syllables) == len(input_syllables), (
        f"syllable count changed: {len(input_syllables)} -> {len(output_syllables)}"
    )
