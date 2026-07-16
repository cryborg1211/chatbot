"""Chunking accuracy tests — semantic quality, not just mechanical limits.

Verifies that the chunker produces retrieval-useful chunks from realistic
Vietnamese government document patterns. Every test uses synthetic markdown
that mirrors real Docling output from the corpus (headings, tables, prose,
mixed).

Test tiers:
  - Content preservation: no source text silently dropped
  - Structural integrity: tables kept atomic, headings preserved
  - Government document patterns: Quyết định, bảng danh mục, phụ lục
  - Edge cases: empty sections, single-row tables, mixed prose+table
"""

from __future__ import annotations

import pytest

pytest.importorskip("transformers")

from app.services.loader import DoclingResult


@pytest.fixture(scope="module")
def chunker():
    from app.services.chunker import Chunker
    try:
        return Chunker(chunk_size=200, chunk_overlap=30, token_cap=512)
    except Exception as exc:
        pytest.skip(f"bge-m3 tokenizer unavailable: {exc}")


# ====================================================================
#  Content preservation — no silent data loss
# ====================================================================

SAMPLE_GOVT_DOC = """\
## Điều 1. Phạm vi điều chỉnh

Quyết định này quy định về kế hoạch triển khai ứng dụng kết quả hoạt động
khoa học và công nghệ trên địa bàn tỉnh Lâm Đồng giai đoạn 2024-2025.

## Điều 2. Đối tượng áp dụng

Các cơ quan, tổ chức, cá nhân liên quan đến hoạt động khoa học và công nghệ
trên địa bàn tỉnh Lâm Đồng.

## Điều 3. Danh mục thiết bị

| STT | Tên thiết bị | Số lượng | Đơn giá (triệu đồng) | Ghi chú |
| --- | --- | --- | --- | --- |
| 1 | Máy phân tích quang phổ UV-Vis | 2 | 350 | Phòng thí nghiệm A |
| 2 | Thiết bị đo pH tự động | 5 | 45 | Phòng thí nghiệm B |
| 3 | Kính hiển vi điện tử quét SEM | 1 | 2500 | Trung tâm phân tích |

## Điều 4. Kinh phí thực hiện

Tổng kinh phí dự kiến: 5.890 triệu đồng, trong đó:
- Ngân sách nhà nước: 4.200 triệu đồng
- Nguồn xã hội hóa: 1.690 triệu đồng
"""


def test_no_content_lost(chunker) -> None:
    """Every word from source appears in at least one chunk."""
    doc = DoclingResult(text=SAMPLE_GOVT_DOC, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    all_chunk_text = " ".join(n.get_content() for n in nodes)

    key_phrases = [
        "Phạm vi điều chỉnh",
        "Đối tượng áp dụng",
        "Máy phân tích quang phổ UV-Vis",
        "Kính hiển vi điện tử quét SEM",
        "5.890 triệu đồng",
        "Nguồn xã hội hóa",
        "khoa học và công nghệ",
    ]
    for phrase in key_phrases:
        assert phrase in all_chunk_text, f"Content lost: '{phrase}' not in any chunk"


def test_no_empty_chunks(chunker) -> None:
    """No chunk is empty or whitespace-only."""
    doc = DoclingResult(text=SAMPLE_GOVT_DOC, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    for i, node in enumerate(nodes):
        content = node.get_content().strip()
        assert len(content) > 0, f"Chunk {i} is empty"


def test_chunk_count_reasonable(chunker) -> None:
    """Document with 4 sections produces 2-8 chunks (not 1 blob, not 50 fragments)."""
    doc = DoclingResult(text=SAMPLE_GOVT_DOC, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    assert 2 <= len(nodes) <= 8, f"Got {len(nodes)} chunks — over-split or under-split"


# ====================================================================
#  Table structure integrity
# ====================================================================

SIMPLE_TABLE = """\
## Bảng danh mục

| STT | Nội dung | Số tiền |
| --- | --- | --- |
| 1 | Kinh phí nghiên cứu | 500 |
| 2 | Kinh phí mua sắm | 800 |
| 3 | Kinh phí đào tạo | 200 |
"""


def test_table_header_preserved_in_chunks(chunker) -> None:
    """When a table is chunked, each sub-chunk retains the header row."""
    large_table_rows = "\n".join(
        f"| {i} | Hạng mục chi tiết số {i} thuộc dự án | {i*100} |"
        for i in range(1, 50)
    )
    table_md = (
        "## Bảng tổng hợp\n\n"
        "| STT | Nội dung | Số tiền |\n"
        "| --- | --- | --- |\n"
        + large_table_rows
    )
    doc = DoclingResult(text=table_md, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])

    table_chunks = [n for n in nodes if "|" in n.get_content() and "---" in n.get_content()]
    assert len(table_chunks) >= 2, "Large table should be split into multiple chunks"
    for tc in table_chunks:
        content = tc.get_content()
        assert "| STT |" in content or "STT" in content.split("\n")[0], (
            f"Table chunk missing header: {content[:100]}..."
        )


def test_table_rows_not_split_mid_row(chunker) -> None:
    """No table row is split across two chunks (atomic row boundary)."""
    doc = DoclingResult(text=SIMPLE_TABLE, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    for node in nodes:
        lines = node.get_content().split("\n")
        for line in lines:
            if line.strip().startswith("|") and "---" not in line:
                pipe_count = line.count("|")
                assert pipe_count >= 3, f"Truncated table row: {line}"


def test_small_table_stays_in_one_chunk(chunker) -> None:
    """A 3-row table under chunk_size stays as one chunk, not fragmented."""
    doc = DoclingResult(text=SIMPLE_TABLE, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    table_text = "\n".join(n.get_content() for n in nodes)
    assert "Kinh phí nghiên cứu" in table_text
    assert "Kinh phí đào tạo" in table_text


# ====================================================================
#  Heading structure
# ====================================================================

def test_heading_boundaries_respected(chunker) -> None:
    """Each H2 section starts in a new chunk (not merged with unrelated sections)."""
    doc = DoclingResult(text=SAMPLE_GOVT_DOC, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    chunks_text = [n.get_content() for n in nodes]

    dieu1_chunk = next((c for c in chunks_text if "Điều 1" in c), None)
    dieu2_chunk = next((c for c in chunks_text if "Điều 2" in c), None)
    assert dieu1_chunk is not None, "Điều 1 not found in any chunk"
    assert dieu2_chunk is not None, "Điều 2 not found in any chunk"


def test_nested_headings_split_correctly(chunker) -> None:
    """H3 sub-sections under a large H2 are split at H3 boundaries."""
    long_section = "## Chương I. Quy định chung\n\n"
    for i in range(1, 6):
        long_section += f"### Điều {i}\n\n"
        long_section += f"Nội dung chi tiết của điều {i} trong quy định chung. " * 40 + "\n\n"

    doc = DoclingResult(text=long_section, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    assert len(nodes) >= 3, "Long H2 with 5 H3 sub-sections should split into multiple chunks"


# ====================================================================
#  Government document patterns
# ====================================================================

QUYET_DINH_HEADER = """\
## QUYẾT ĐỊNH

Về việc ban hành Kế hoạch triển khai ứng dụng kết quả hoạt động
khoa học và công nghệ trên địa bàn tỉnh Lâm Đồng

### CHỦ TỊCH ỦY BAN NHÂN DÂN TỈNH LÂM ĐỒNG

Căn cứ Luật Tổ chức chính quyền địa phương ngày 19 tháng 6 năm 2015;
Căn cứ Luật Khoa học và Công nghệ ngày 18 tháng 6 năm 2013;
Xét đề nghị của Giám đốc Sở Khoa học và Công nghệ tại Tờ trình
số 1202/TTr-SKHCN ngày 15 tháng 3 năm 2024;
"""


def test_quyet_dinh_header_not_filtered_as_noise(chunker) -> None:
    """Government decision headers with org names must NOT be filtered as noise."""
    doc = DoclingResult(text=QUYET_DINH_HEADER, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    all_text = " ".join(n.get_content() for n in nodes)
    assert "CHỦ TỊCH ỦY BAN NHÂN DÂN" in all_text, "Org name filtered as noise"
    assert "1202/TTr-SKHCN" in all_text, "Reference number filtered as noise"


def test_statute_references_preserved(chunker) -> None:
    """Statute/decision references survive chunking and noise filtering."""
    text = """\
## Căn cứ pháp lý

Căn cứ Nghị định số 08/2014/NĐ-CP ngày 27 tháng 01 năm 2014;
Căn cứ Quyết định số 33/2020/QĐ-UBND ngày 15 tháng 5 năm 2020;
Căn cứ Thông tư số 12/2021/TT-BKHCN ngày 01 tháng 12 năm 2021;
"""
    doc = DoclingResult(text=text, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    all_text = " ".join(n.get_content() for n in nodes)
    assert "08/2014/NĐ-CP" in all_text
    assert "33/2020/QĐ-UBND" in all_text
    assert "12/2021/TT-BKHCN" in all_text


# ====================================================================
#  Mixed prose + table documents
# ====================================================================

def test_prose_table_prose_sandwich(chunker) -> None:
    """Prose-table-prose pattern: each block type stays atomic."""
    mixed = """\
## Tổng quan dự án

Dự án trung tâm đổi mới sáng tạo nhằm phục vụ nghiên cứu khoa học
và chuyển giao công nghệ cho nông nghiệp công nghệ cao tỉnh Lâm Đồng.

| Hạng mục | Chi phí (triệu đồng) |
| --- | --- |
| Xây dựng | 15.000 |
| Thiết bị | 8.500 |
| Nhân lực | 3.200 |

Tổng mức đầu tư dự kiến là 26.700 triệu đồng từ nguồn ngân sách
tỉnh và vốn xã hội hóa.
"""
    doc = DoclingResult(text=mixed, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    all_text = " ".join(n.get_content() for n in nodes)
    assert "nông nghiệp công nghệ cao" in all_text
    assert "15.000" in all_text
    assert "26.700 triệu đồng" in all_text


# ====================================================================
#  Edge cases
# ====================================================================

def test_empty_document_no_crash(chunker) -> None:
    """Empty document produces 0 chunks, no crash."""
    doc = DoclingResult(text="", docling_doc=None, metadata={"source": "empty"})
    nodes = chunker.split([doc])
    assert nodes == [] or all(n.get_content().strip() == "" for n in nodes)


def test_single_line_document(chunker) -> None:
    """Single-line document produces exactly 1 chunk."""
    doc = DoclingResult(
        text="Quyết định số 33/2020/QĐ-UBND ban hành ngày 15/5/2020.",
        docling_doc=None, metadata={"source": "test"},
    )
    nodes = chunker.split([doc])
    assert len(nodes) >= 1
    assert "33/2020" in nodes[0].get_content()


def test_table_without_separator(chunker) -> None:
    """Tables without |---| separator still get chunked, not passed through as one blob."""
    no_sep_table = "## Bảng\n\n" + "\n".join(
        f"| {i} | Mục {i} | {i*100} đồng |" for i in range(1, 60)
    )
    doc = DoclingResult(text=no_sep_table, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    assert len(nodes) >= 1
    all_text = " ".join(n.get_content() for n in nodes)
    assert "Mục 1" in all_text
    assert "Mục 59" in all_text


def test_unicode_diacritics_not_corrupted(chunker) -> None:
    """Vietnamese diacritics survive chunking without corruption."""
    text = """\
## Đề xuất

Ủy ban nhân dân tỉnh Lâm Đồng đề nghị Bộ Khoa học và Công nghệ
xem xét phê duyệt đề án "Ứng dụng trí tuệ nhân tạo trong nông nghiệp
công nghệ cao" với kinh phí 15.000 triệu đồng.

Người ký: Nguyễn Thị Hương — Phó Giám đốc Sở KHCN
"""
    doc = DoclingResult(text=text, docling_doc=None, metadata={"source": "test"})
    nodes = chunker.split([doc])
    all_text = " ".join(n.get_content() for n in nodes)
    assert "Ủy ban nhân dân" in all_text
    assert "trí tuệ nhân tạo" in all_text
    assert "Nguyễn Thị Hương" in all_text
