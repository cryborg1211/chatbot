"""Fork 3 — OCR double-pass OR-merge regression tests.

Two behaviours, both via MOCKED converters (no real PDF / Docling model):
  1. ``_convert_pdf_batched`` surfaces the (start, end) ranges of skipped
     batches instead of silently discarding them.
  2. ``_load_docling`` OR-merges the digital-pass and OCR-retry-pass failed
     ranges (union, not overwrite) into ``DoclingResult.partial_reason``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import loader
from app.services.loader import DoclingResult, _convert_pdf_batched


def _fake_result(markdown: str):
    """Mimic docling convert() -> result.document.export_to_markdown()."""
    document = SimpleNamespace(export_to_markdown=lambda: markdown, pages={})
    return SimpleNamespace(document=document)


def test_convert_pdf_batched_reports_failed_ranges(tmp_path: Path) -> None:
    # 25 pages -> 3 batches at batch size 10: (1-10), (11-20), (21-25).
    # Make the middle batch (11-20) raise; the others succeed.
    def convert(_path, page_range=None, raises_on_error=False):  # noqa: ANN001
        start = page_range[0] if page_range else 1
        if start == 11:
            raise RuntimeError("bad_alloc: simulated OOM on batch 11-20")
        return _fake_result(f"page batch starting {start}")

    converter = SimpleNamespace(convert=convert)
    dummy_pdf = tmp_path / "doc.pdf"
    dummy_pdf.write_bytes(b"%PDF-fake")

    merged, last_doc, failed_count, failed_ranges = _convert_pdf_batched(
        dummy_pdf, converter, total_pages=25, original_name="doc.pdf",
    )

    assert failed_count == 1
    assert (11, 20) in failed_ranges
    assert merged  # the surviving batches still produced text


def test_or_merge_across_digital_and_ocr_passes(monkeypatch, tmp_path: Path) -> None:
    """Digital pass drops (11-20); OCR retry drops (21-30). Merged reason lists both.

    Updated for Phase 0: the digital pass now routes through
    ``_convert_pdf_page_ranges`` (per-page runs) before the whole-doc OCR
    safety-net fallback. This test exercises that fallback path: the density
    probe says 'all text', but the digital pass produces no markdown, so the
    near-empty safety net triggers a full ``_convert_pdf_batched`` OCR retry.
    The OR-merge of failed ranges across both passes must still hold.
    """
    # Digital pass (per-page ranges): drops (11-20), produces no text.
    def fake_convert_pdf_page_ranges(_path, _converter, _ranges, _name, **_kwargs):  # noqa: ANN001
        return "", None, [(11, 20)]

    # OCR safety-net pass: produces real text, but drops a DIFFERENT batch.
    def fake_convert_pdf_batched(_path, _converter, _total, _name, **_kwargs):  # noqa: ANN001
        return "Nội dung sau khi OCR đầy đủ nhiều trang văn bản.", object(), 1, [(21, 30)]

    monkeypatch.setattr(loader, "_convert_pdf_page_ranges", fake_convert_pdf_page_ranges)
    monkeypatch.setattr(loader, "_convert_pdf_batched", fake_convert_pdf_batched)
    monkeypatch.setattr(loader, "_get_pdf_page_count", lambda _p: 30)
    monkeypatch.setattr(loader, "_get_available_ram_gb", lambda: 8.0)
    # Density probe: optimistically classifies every page as 'text', so we
    # go through the digital path first — which then produces nothing.
    monkeypatch.setattr(loader, "_probe_page_density", lambda _p, _t: ["text"] * 30)
    # Force the "near-empty digital pass -> whole-doc OCR retry" safety net.
    monkeypatch.setattr(loader, "_is_near_empty", lambda _md, _doc: True)
    # Skip the OCR converter build (would load EasyOCR) and the quality gate.
    monkeypatch.setattr(loader, "_ocr_pdf_converter", lambda: object())
    monkeypatch.setattr(loader, "_lightweight_pdf_converter", lambda: object())
    monkeypatch.setattr(loader, "_bare_pdf_converter", lambda: object())
    monkeypatch.setattr(loader, "_assess_pdf_quality", lambda *_a, **_k: (True, ""))

    result = loader._load_docling(b"%PDF-fake-bytes", "scan.pdf", ".pdf")

    assert len(result) == 1
    docling_result: DoclingResult = result[0]
    assert docling_result.partial is True
    assert docling_result.partial_reason is not None
    # OR-merge (union), not overwrite: BOTH ranges present.
    assert "11-20" in docling_result.partial_reason
    assert "21-30" in docling_result.partial_reason


# ====================================================================
#  Phase 0 — per-page text-density classifier + run grouper.
#  These are pure, model-free units: the routing decision happens BEFORE
#  any Docling parse, so they can be tested without loading any model.
# ====================================================================


def test_classify_page_density_text_vs_scan() -> None:
    """A page with a real Vietnamese text layer is 'text'; image-only is 'scan'."""
    from app.services.loader import _classify_page_density

    # A normal digital page with Vietnamese content.
    assert _classify_page_density(
        "Quyết định số 1202/QĐ-BKHCN về việc ban hành kế hoạch triển khai "
        "ứng dụng kết quả hoạt động khoa học và công nghệ."
    ) == "text"

    # Image-only page: empty text layer.
    assert _classify_page_density("") == "scan"

    # Near-empty: too few chars to be a real text page.
    assert _classify_page_density("abc") == "scan"

    # Symbol soup: lots of glyphs but no letters (corrupt text layer).
    assert _classify_page_density("1234!@#$%^&*()_+-={}[]|\\:;<>?,./~`") == "scan"


def test_classify_page_density_vietnamese_compound_words() -> None:
    """Diacritics-heavy Vietnamese text must NOT be mis-classified as symbol soup.

    This is the critical regression guard: the alpha-ratio threshold must be
    low enough that legitimate Vietnamese with diacritics passes as 'text'.
    """
    from app.services.loader import _classify_page_density

    vietnamese = (
        "Nguyễn Văn A, Trưởng phòng Khoa học Công nghệ, thông báo kế hoạch "
        "hoạt động năm 2023. Các đơn vị liên quan thực hiện theo quy định."
    )
    assert _classify_page_density(vietnamese) == "text"


def test_group_density_runs_preserves_order() -> None:
    """A hybrid doc (text-text-scan-scan-text) becomes 3 ordered runs."""
    from app.services.loader import _group_density_runs

    densities = ["text", "text", "scan", "scan", "text"]
    runs = _group_density_runs(densities)
    assert runs == [(1, 2, "text"), (3, 4, "scan"), (5, 5, "text")]


def test_group_density_runs_single_class() -> None:
    """A pure-scan or pure-text doc compresses to one run."""
    from app.services.loader import _group_density_runs

    assert _group_density_runs(["scan"] * 5) == [(1, 5, "scan")]
    assert _group_density_runs(["text"] * 3) == [(1, 3, "text")]
    assert _group_density_runs([]) == []


def test_mark_route_updates_fields() -> None:
    """The manifest mutator updates only the named fields of one page."""
    from app.services.loader import _mark_route

    routes = [
        {"page": 1, "density": "text", "chars": 0, "ocr_used": False, "dropped": False},
        {"page": 2, "density": "scan", "chars": 0, "ocr_used": False, "dropped": False},
    ]
    _mark_route(routes, 2, ocr_used=True)
    assert routes[0]["ocr_used"] is False
    assert routes[1]["ocr_used"] is True
    # Out-of-range is ignored, not an error.
    _mark_route(routes, 99, ocr_used=True)
    assert all(r["ocr_used"] in (False, True) for r in routes)


# ====================================================================
#  Multi-file OOM fix — converter cache release between documents.
#  Without this, the four @lru_cache(maxsize=1) PDF converters
#  (lightweight/bare/ocr/default) stay resident forever, so RAM only
#  climbs across a sequential multi-file batch and never returns to
#  baseline — confirmed as the real cause of multi-PDF-upload OOM.
# ====================================================================


def test_release_pdf_converter_caches_clears_all_four(monkeypatch) -> None:
    """release_pdf_converter_caches() clears every cached converter builder."""
    from app.services import loader

    calls: list[str] = []

    def _fake_clear(name):
        def _clear():
            calls.append(name)
        return _clear

    # Patch cache_clear on each lru_cache-wrapped function.
    for name in ("_lightweight_pdf_converter", "_bare_pdf_converter",
                 "_ocr_pdf_converter", "_default_converter"):
        fn = getattr(loader, name)
        monkeypatch.setattr(fn, "cache_clear", _fake_clear(name))

    loader.release_pdf_converter_caches()

    assert set(calls) == {
        "_lightweight_pdf_converter", "_bare_pdf_converter",
        "_ocr_pdf_converter", "_default_converter",
    }


def test_release_pdf_converter_caches_forces_rebuild_on_next_call() -> None:
    """After release, the next call to a cached converter builds a fresh instance
    (not the same cached object) — proves the cache genuinely drops the old one,
    not just resets an internal counter."""
    from app.services import loader

    # _default_converter has the simplest body (no OCR/table config needed).
    first = loader._default_converter()
    same_again = loader._default_converter()
    assert same_again is first, "sanity: lru_cache should return the same object before release"

    loader.release_pdf_converter_caches()

    second = loader._default_converter()
    assert second is not first, "converter must be rebuilt fresh after cache release"
