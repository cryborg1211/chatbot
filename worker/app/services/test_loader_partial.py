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
    """Digital pass drops (11-20); OCR retry drops (21-30). Merged reason lists both."""
    calls = {"n": 0}

    def fake_convert_pdf_batched(_path, _converter, _total, _name, **_kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            # Digital pass: near-empty markdown to force the OCR retry branch,
            # and reports batch (11-20) dropped.
            return "", None, 1, [(11, 20)]
        # OCR retry pass: real text, but reports a DIFFERENT dropped batch.
        return "Nội dung sau khi OCR đầy đủ nhiều trang văn bản.", object(), 1, [(21, 30)]

    monkeypatch.setattr(loader, "_convert_pdf_batched", fake_convert_pdf_batched)
    monkeypatch.setattr(loader, "_get_pdf_page_count", lambda _p: 30)
    monkeypatch.setattr(loader, "_get_available_ram_gb", lambda: 8.0)
    # Force the "near-empty digital pass -> OCR retry" branch.
    monkeypatch.setattr(loader, "_is_near_empty", lambda _md, _doc: calls["n"] == 1)
    # Skip the OCR converter build (would load EasyOCR) and the quality gate.
    monkeypatch.setattr(loader, "_ocr_pdf_converter", lambda: object())
    monkeypatch.setattr(loader, "_lightweight_pdf_converter", lambda: object())
    monkeypatch.setattr(loader, "_assess_pdf_quality", lambda *_a, **_k: (True, ""))

    result = loader._load_docling(b"%PDF-fake-bytes", "scan.pdf", ".pdf")

    assert len(result) == 1
    docling_result: DoclingResult = result[0]
    assert docling_result.partial is True
    assert docling_result.partial_reason is not None
    # OR-merge (union), not overwrite: BOTH ranges present.
    assert "11-20" in docling_result.partial_reason
    assert "21-30" in docling_result.partial_reason
