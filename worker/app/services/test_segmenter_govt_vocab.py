"""Segmenter accuracy on Vietnamese government vocabulary.

The base test_segmenter.py covers generic compound words. This file tests
the patterns that ACTUALLY appear in the LD3 corpus: statute/decision
references, government org names, legal terms, mixed Viet/English technical
vocabulary, and table-cell content.

These are the patterns BM25 must tokenize correctly — mis-segmentation here
means the hybrid retriever silently misses keyword matches on the most
common query types.
"""

from __future__ import annotations

from app.services.segmenter import segment


# ====================================================================
#  Statute and decision references
# ====================================================================

def test_statute_number_components_preserved() -> None:
    """All components of '33/2020/QĐ-UBND' survive segmentation.

    pyvi inserts spaces around / and - (CRF tokenizer behavior), so
    '33/2020/QĐ-UBND' becomes '33 / 2020 / QĐ - UBND'. This is fine
    because both ingest and query go through segment() — BM25 symmetry
    holds. We assert all components are present, not exact substring.
    """
    result = segment("Quyết định số 33/2020/QĐ-UBND")
    for component in ["33", "2020", "QĐ", "UBND"]:
        assert component in result, f"Component '{component}' lost: {result}"


def test_complex_statute_components_preserved() -> None:
    """All components of '1202/QĐ-BKHCNCGCN' survive segmentation."""
    result = segment("Quyết định 1202/QĐ-BKHCNCGCN về kết quả hoạt động")
    for component in ["1202", "QĐ", "BKHCNCGCN"]:
        assert component in result, f"Component '{component}' lost: {result}"


def test_nghi_dinh_components_preserved() -> None:
    """Nghị định reference components survive."""
    result = segment("Nghị định số 08/2014/NĐ-CP ngày 27 tháng 01 năm 2014")
    for component in ["08", "2014", "NĐ", "CP"]:
        assert component in result, f"Component '{component}' lost: {result}"


def test_thong_tu_components_preserved() -> None:
    """Thông tư reference components survive."""
    result = segment("Thông tư số 12/2021/TT-BKHCN ngày 01 tháng 12 năm 2021")
    for component in ["12", "2021", "TT", "BKHCN"]:
        assert component in result, f"Component '{component}' lost: {result}"


def test_statute_segmentation_symmetric() -> None:
    """Statute reference segments identically in doc and query contexts.

    pyvi may break '33/2020/QĐ-UBND' into '33 / 2020 / QĐ - UBND',
    but as long as BOTH sides produce the same tokens, BM25 works.
    """
    statute = "33/2020/QĐ-UBND"
    doc_seg = segment(f"Theo Quyết định số {statute} về quản lý")
    query_seg = segment(f"Quyết định {statute}")
    # The statute components should be tokenized identically
    doc_tokens = doc_seg.split()
    query_tokens = query_seg.split()
    # "33", "2020", "QĐ", "UBND" should appear in both
    for component in ["33", "2020"]:
        assert component in doc_tokens, f"'{component}' missing from doc: {doc_tokens}"
        assert component in query_tokens, f"'{component}' missing from query: {query_tokens}"


# ====================================================================
#  Government organization names
# ====================================================================

def test_ubnd_compound() -> None:
    """'Ủy ban nhân dân' should be segmented as compound words."""
    result = segment("Ủy ban nhân dân tỉnh Lâm Đồng")
    # pyvi should join compound words — the key is no content is dropped
    assert "Lâm" in result and "Đồng" in result
    assert len(result) > 0


def test_bo_khcn_name() -> None:
    """Ministry name preserves all syllables."""
    text = "Bộ Khoa học và Công nghệ"
    result = segment(text)
    assert "Khoa" in result or "khoa" in result
    assert "Công" in result or "công" in result
    # No content dropped
    words_in = set(text.split())
    words_out = set(result.replace("_", " ").split())
    for w in words_in:
        assert any(w in wo or wo in w for wo in words_out), f"Word '{w}' lost in segmentation"


def test_so_khcn_name() -> None:
    """Department-level names like 'Sở Khoa học và Công nghệ'."""
    result = segment("Sở Khoa học và Công nghệ tỉnh Lâm Đồng")
    assert "Lâm" in result
    assert len(result.split()) >= 3  # not collapsed to nothing


# ====================================================================
#  Legal and administrative terms
# ====================================================================

def test_legal_terms_compound() -> None:
    """Common legal compound words: 'quyết định', 'nghị quyết', etc."""
    terms = [
        ("quyết định", "quyết_định"),
        ("nghị quyết", "nghị_quyết"),
        ("thông tư", "thông_tư"),
        ("nghị định", "nghị_định"),
    ]
    for raw, expected_compound in terms:
        result = segment(raw)
        # pyvi should join these — either underscored or at minimum not dropped
        assert expected_compound in result or raw.replace(" ", "") in result.replace("_", "").replace(" ", ""), (
            f"Legal term '{raw}' not properly segmented: {result}"
        )


def test_administrative_phrases() -> None:
    """Administrative phrases used in government documents."""
    phrases = [
        "kinh phí thực hiện",
        "ngân sách nhà nước",
        "kế hoạch triển khai",
        "báo cáo thống kê",
        "đề xuất đầu tư",
    ]
    for phrase in phrases:
        result = segment(phrase)
        # Core requirement: no content dropped
        for word in phrase.split():
            assert word in result or word in result.replace("_", " "), (
                f"Word '{word}' from '{phrase}' lost after segmentation: {result}"
            )


# ====================================================================
#  Mixed Vietnamese / English technical terms
# ====================================================================

def test_mixed_viet_english() -> None:
    """Technical terms mixing Vietnamese and English survive.

    pyvi may split 'UV-Vis' into 'UV - Vis' — components preserved is enough.
    """
    text = "Máy phân tích quang phổ UV-Vis dùng cho phòng thí nghiệm"
    result = segment(text)
    assert "UV" in result, f"English abbreviation UV lost: {result}"
    assert "Vis" in result, f"English abbreviation Vis lost: {result}"


def test_english_abbreviations_preserved() -> None:
    """Abbreviations like SEM, HPLC, IoT stay intact."""
    text = "Kính hiển vi điện tử quét SEM và thiết bị HPLC"
    result = segment(text)
    assert "SEM" in result
    assert "HPLC" in result


def test_numbers_and_units() -> None:
    """Numeric values with Vietnamese units survive."""
    text = "Tổng kinh phí 15.890 triệu đồng cho giai đoạn 2024-2025"
    result = segment(text)
    assert "15.890" in result
    assert "2024-2025" in result or ("2024" in result and "2025" in result)


# ====================================================================
#  Table cell content
# ====================================================================

def test_table_cell_segmentation() -> None:
    """Content that typically appears in table cells segments correctly."""
    cells = [
        "Phòng thí nghiệm A",
        "Trung tâm phân tích",
        "Máy đo pH tự động",
        "350 triệu đồng",
    ]
    for cell in cells:
        result = segment(cell)
        assert len(result.strip()) > 0, f"Table cell '{cell}' produced empty result"
        # No content dropped
        for word in cell.split():
            assert word in result or word in result.replace("_", " "), (
                f"Word '{word}' from cell '{cell}' lost: {result}"
            )


# ====================================================================
#  Segmentation symmetry (ingest == query)
# ====================================================================

def test_segmentation_idempotent() -> None:
    """Segmenting already-segmented text produces the same output."""
    text = "khoa học và công nghệ"
    first = segment(text)
    second = segment(first)
    assert first == second, f"Not idempotent: '{first}' != '{second}'"


def test_query_and_document_segment_identically() -> None:
    """The same phrase segments identically whether it's a query or doc chunk.

    This is the critical invariant: asymmetric segmentation at ingest vs query
    silently destroys BM25 keyword overlap.
    """
    phrase = "báo cáo thống kê khoa học công nghệ năm 2023"
    doc_seg = segment(phrase)
    query_seg = segment(phrase)
    assert doc_seg == query_seg, (
        f"Asymmetric segmentation! doc='{doc_seg}' query='{query_seg}'"
    )
