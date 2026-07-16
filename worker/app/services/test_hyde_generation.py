"""HyDE (Hypothetical Document Embeddings) tests.

Tests the gating logic, output quality constraints, and failure fallback.
Uses a mock LLM to avoid real model calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.hyde import generate_hypothetical_answer


def _make_mock_llm(response: str):
    """Create a mock LLM that streams the response word by word."""
    llm = MagicMock()

    async def mock_stream(messages):
        for word in response.split():
            yield word + " "

    llm.stream_chat = mock_stream
    return llm


def _run(coro):
    """Run an async function synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ====================================================================
#  Gating: when HyDE should and shouldn't fire
# ====================================================================

def test_short_query_generates_answer() -> None:
    """Queries under max_query_chars produce a hypothetical answer."""
    llm = _make_mock_llm(
        "Quyết định 33/2020 quy định về quản lý hoạt động khoa học và công nghệ "
        "trên địa bàn tỉnh Lâm Đồng."
    )
    result = _run(generate_hypothetical_answer(
        "Quyết định 33/2020 về gì?", llm, max_query_chars=80,
    ))
    assert result is not None
    assert len(result) > 20


def test_long_query_skipped() -> None:
    """Queries exceeding max_query_chars return None (skip HyDE)."""
    llm = _make_mock_llm("Should not be called")
    long_query = "Cho tôi biết chi tiết về " + "nội dung " * 20
    assert len(long_query) > 80

    result = _run(generate_hypothetical_answer(
        long_query, llm, max_query_chars=80,
    ))
    assert result is None


def test_empty_query_skipped() -> None:
    """Empty query returns None."""
    llm = _make_mock_llm("Should not be called")
    result = _run(generate_hypothetical_answer("", llm))
    assert result is None


def test_none_query_skipped() -> None:
    """None-ish query returns None."""
    llm = _make_mock_llm("Should not be called")
    result = _run(generate_hypothetical_answer(None, llm))
    assert result is None


# ====================================================================
#  Failure fallback
# ====================================================================

def test_llm_exception_returns_none() -> None:
    """If the LLM throws, return None (fallback to raw query)."""
    llm = MagicMock()

    async def failing_stream(messages):
        raise RuntimeError("LLM connection timeout")
        yield  # make it a generator

    llm.stream_chat = failing_stream

    result = _run(generate_hypothetical_answer("test query", llm))
    assert result is None


def test_llm_returns_too_short() -> None:
    """If LLM returns < 20 chars, treat as garbage and return None."""
    llm = _make_mock_llm("OK")  # only 3 chars
    result = _run(generate_hypothetical_answer("test query", llm))
    assert result is None


def test_llm_returns_empty_stream() -> None:
    """If LLM streams nothing, return None."""
    llm = MagicMock()

    async def empty_stream(messages):
        return
        yield

    llm.stream_chat = empty_stream

    result = _run(generate_hypothetical_answer("test query", llm))
    assert result is None


# ====================================================================
#  Output quality
# ====================================================================

def test_output_is_vietnamese() -> None:
    """The hypothetical answer should be Vietnamese text."""
    vietnamese_answer = (
        "Quyết định số 33/2020/QĐ-UBND ban hành Quy chế quản lý hoạt động "
        "khoa học và công nghệ trên địa bàn tỉnh Lâm Đồng. Quy chế này quy "
        "định về thẩm quyền, trình tự thủ tục quản lý các nhiệm vụ KH&CN."
    )
    llm = _make_mock_llm(vietnamese_answer)
    result = _run(generate_hypothetical_answer(
        "Quyết định 33/2020 về gì?", llm,
    ))
    assert result is not None
    # Vietnamese text should contain diacritics
    assert any(c in result for c in "àáảãạăắằẳẵặâấầẩẫậ")


def test_output_stripped() -> None:
    """Leading/trailing whitespace stripped from output."""
    llm = _make_mock_llm("  Nội dung câu trả lời giả định đủ dài.  ")
    result = _run(generate_hypothetical_answer("test", llm))
    assert result is not None
    assert not result.startswith(" ")
    assert not result.endswith(" ")


# ====================================================================
#  Integration with query pipeline expectations
# ====================================================================

def test_hyde_result_suitable_for_embedding() -> None:
    """The HyDE output should be plain text suitable for bge-m3 embedding.

    No JSON, no markdown formatting, no special tokens — just Vietnamese prose
    that a dense embedder can meaningfully encode.
    """
    answer = (
        "Báo cáo thống kê khoa học công nghệ năm 2023 tổng hợp số liệu "
        "về nhân lực, kinh phí, đề tài nghiên cứu và kết quả ứng dụng "
        "trên địa bàn tỉnh Lâm Đồng."
    )
    llm = _make_mock_llm(answer)
    result = _run(generate_hypothetical_answer(
        "báo cáo thống kê 2023", llm,
    ))
    assert result is not None
    # Should not contain JSON-like or markdown-like artifacts
    assert "{" not in result
    assert "```" not in result
    assert "##" not in result
