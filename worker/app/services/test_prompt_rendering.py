"""Prompt template rendering tests — verify full chunk text reaches the LLM.

Guards against the snippet truncation regression (debt #23): the Jinja2
template must render ``doc.text`` (full chunk), NOT ``doc.snippet`` (500-char
truncation). Also verifies the template handles edge cases (no sources,
Vietnamese diacritics in content).
"""

from __future__ import annotations

from app.services.prompt_builder import PromptBuilder
from app.services.retriever import RetrievedSource


def _make_source(text: str, title: str = "test.pdf", score: float = 0.85) -> RetrievedSource:
    return RetrievedSource(
        id="test-id-001",
        document_id="doc-001",
        title=title,
        text=text,
        snippet=text[:500] + "..." if len(text) > 500 else text,
        score=score,
    )


def test_full_text_reaches_llm_not_snippet() -> None:
    """The rendered prompt contains the FULL chunk text, not the 500-char snippet."""
    long_text = "Đây là nội dung chi tiết. " * 100  # ~2600 chars
    assert len(long_text) > 500

    source = _make_source(long_text)
    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert long_text in rendered, (
        "Full text missing from rendered prompt — template may still use doc.snippet"
    )
    tail_phrase = long_text[-50:]
    assert tail_phrase in rendered, (
        f"Tail of chunk not in prompt — truncated at ~500 chars? Missing: {tail_phrase!r}"
    )


def test_multiple_sources_all_present() -> None:
    """All sources appear in the prompt, numbered [1], [2], etc."""
    sources = [
        _make_source("Nội dung tài liệu thứ nhất về khoa học.", title="doc1.pdf"),
        _make_source("Nội dung tài liệu thứ hai về công nghệ.", title="doc2.pdf"),
        _make_source("Nội dung tài liệu thứ ba về nông nghiệp.", title="doc3.pdf"),
    ]
    builder = PromptBuilder()
    rendered = builder.build_system(sources)

    for i, s in enumerate(sources, 1):
        assert f"[{i}]" in rendered, f"Source number [{i}] missing"
        assert s.title in rendered, f"Source title {s.title} missing"
        assert s.text in rendered, f"Source text missing for [{i}]"


def test_no_sources_fallback_message() -> None:
    """Empty source list renders the 'no documents found' fallback."""
    builder = PromptBuilder()
    rendered = builder.build_system([])
    assert "Không tìm thấy tài liệu" in rendered


def test_vietnamese_diacritics_in_prompt() -> None:
    """Vietnamese text with heavy diacritics renders without corruption."""
    text = (
        "Quyết định số 33/2020/QĐ-UBND ban hành về việc phê duyệt "
        "Đề án ứng dụng trí tuệ nhân tạo trong nông nghiệp công nghệ cao "
        "tỉnh Lâm Đồng, giai đoạn 2024-2025. Người ký: Trần Đức Quận."
    )
    source = _make_source(text, title="QD_33_2020.pdf")
    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert "Quyết định số 33/2020" in rendered
    assert "trí tuệ nhân tạo" in rendered
    assert "Trần Đức Quận" in rendered


def test_table_content_in_prompt() -> None:
    """Table markdown passes through to the LLM prompt intact."""
    table_text = (
        "| STT | Thiết bị | Số lượng |\n"
        "| --- | --- | --- |\n"
        "| 1 | Máy phân tích UV-Vis | 2 |\n"
        "| 2 | Kính hiển vi SEM | 1 |\n"
    )
    source = _make_source(table_text, title="danh_muc.pdf")
    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert "Máy phân tích UV-Vis" in rendered
    assert "Kính hiển vi SEM" in rendered
    assert "| 2 |" in rendered


def test_prompt_contains_system_instructions() -> None:
    """The rendered prompt includes the Vietnamese system instructions."""
    source = _make_source("Test content.")
    builder = PromptBuilder()
    rendered = builder.build_system([source])

    assert "QUY TẮC TRẢ LỜI BẮT BUỘC" in rendered
    assert "TÀI LIỆU THAM KHẢO" in rendered
