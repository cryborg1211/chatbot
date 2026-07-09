"""HIGH#1 — noise-filter false-positive regression tests.

Pure-function tests on ``_is_noise`` — no model / network dependency, so these
are Fully-Automated tier. They prove:
  1. ordinary Vietnamese Titlecase headings / proper nouns are NOT deleted,
  2. real signature blocks ARE still deleted (no regression),
  3. legitimate short reference numbers survive (debt #9 digit-strip fix).
"""

from __future__ import annotations

from app.services.chunker import _is_noise


def test_vietnamese_heading_not_filtered() -> None:
    # Ordinary org-name heading with no signature/role word — must survive.
    assert _is_noise("Ủy Ban Nhân Dân Tỉnh Lâm Đồng") is False


def test_true_signature_still_filtered() -> None:
    # Real signature block (role word + name) — must still be filtered.
    assert _is_noise("KT. GIÁM ĐỐC\nNguyễn Văn A") is True


def test_bare_role_line_still_filtered() -> None:
    # Bare role line matched by the existing _NOISE_PATTERNS — must still filter.
    assert _is_noise("GIÁM ĐỐC") is True


def test_reference_number_not_filtered() -> None:
    # Legitimate reference numbers must survive (digit-strip bug fix, debt #9).
    assert _is_noise("1202/QĐ-BKHCNCGCN") is False
    assert _is_noise("12/QĐ") is False
