"""
EDA script for lamdong_docs + lamdong_pdf
Mục tiêu: phân tích chất lượng data cho RAG pipeline
"""

import os
import re
import io
import warnings
warnings.filterwarnings("ignore")

import fitz                        # PyMuPDF
import docx                        # python-docx (cho .docx)
import olefile                     # đọc binary .doc
import tiktoken
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import seaborn as sns
from collections import Counter
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────
PDF_DIR  = Path("data/lamdong_pdf")
DOC_DIR  = Path("data/lamdong_docs")
OUT_DIR  = Path("data/eda_output")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ENC = tiktoken.get_encoding("cl100k_base")   # GPT-4 / text-embedding-3 tokenizer
MIN_TEXT_CHARS = 50                           # ngưỡng coi là "có text"
SCANNED_CHARS_PER_PAGE = 100                  # dưới ngưỡng này → PDF scan

# ── Helpers ───────────────────────────────────────────────────────────────
DOC_TYPE_MAP = {
    "BC": "Báo cáo",
    "KH": "Kế hoạch",
    "QD": "Quyết định",
    "QĐ": "Quyết định",
    "TB": "Thông báo",
    "CV": "Công văn",
    "NQ": "Nghị quyết",
    "GM": "Giấy mời",
    "TTr": "Tờ trình",
    "TT": "Thông tư",
    "KL": "Kết luận",
    "DS": "Danh sách",
    "BB": "Biên bản",
    "CT": "Chỉ thị",
    "GU": "Giấy ủy quyền",
}

def guess_doc_type(fname: str) -> str:
    stem = Path(fname).stem
    for prefix, label in DOC_TYPE_MAP.items():
        if re.match(rf"(?i)^{re.escape(prefix)}[\s_\-\d]", stem):
            return label
    return "Khác"


def extract_pdf(path: Path) -> dict:
    try:
        doc = fitz.open(str(path))
        pages = doc.page_count
        all_text = "".join(page.get_text() for page in doc)
        doc.close()
        chars = len(all_text.strip())
        words = len(all_text.split())
        tokens = len(ENC.encode(all_text)) if chars > 0 else 0
        is_scanned = pages > 0 and (chars / pages) < SCANNED_CHARS_PER_PAGE
        return dict(pages=pages, chars=chars, words=words,
                    tokens=tokens, is_scanned=is_scanned, error=None)
    except Exception as e:
        return dict(pages=0, chars=0, words=0, tokens=0,
                    is_scanned=False, error=str(e))


def extract_docx(path: Path) -> dict:
    try:
        d = docx.Document(str(path))
        text = "\n".join(p.text for p in d.paragraphs)
        chars = len(text.strip())
        words = len(text.split())
        tokens = len(ENC.encode(text)) if chars > 0 else 0
        return dict(pages=None, chars=chars, words=words,
                    tokens=tokens, is_scanned=False, error=None)
    except Exception as e:
        return dict(pages=None, chars=0, words=0, tokens=0,
                    is_scanned=False, error=str(e))


def extract_doc_binary(path: Path) -> dict:
    """Đọc .doc (Word 97-2003) qua olefile → WordDocument stream."""
    try:
        if not olefile.isOleFile(str(path)):
            raise ValueError("Not OLE file")
        ole = olefile.OleFileIO(str(path))
        stream = ole.openstream("WordDocument").read()
        # Lấy text theo heuristic: decode UTF-16 LE hoặc latin-1, lọc ký tự
        try:
            text = stream.decode("utf-16-le", errors="ignore")
        except Exception:
            text = stream.decode("latin-1", errors="ignore")
        # Lọc chỉ giữ lại ký tự có thể đọc
        text = re.sub(r"[^\x20-\x7EÀ-ɏḀ-ỿ-ÿ\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        chars = len(text)
        words = len(text.split())
        tokens = len(ENC.encode(text)) if chars > 0 else 0
        ole.close()
        return dict(pages=None, chars=chars, words=words,
                    tokens=tokens, is_scanned=False, error=None)
    except Exception as e:
        return dict(pages=None, chars=0, words=0, tokens=0,
                    is_scanned=False, error=str(e))


# ── Thu thập dữ liệu ──────────────────────────────────────────────────────
print("🔍 Đang phân tích file...")
rows = []

# PDF
for f in sorted(PDF_DIR.iterdir()):
    if f.suffix.lower() != ".pdf":
        continue
    size = f.stat().st_size
    info = extract_pdf(f)
    rows.append(dict(
        fname=f.name, folder="lamdong_pdf", ext="pdf",
        size_kb=round(size/1024, 1), doc_type=guess_doc_type(f.name),
        is_signed="_Signed" in f.name or ".signed" in f.name.lower(),
        **info
    ))

# DOCX
for f in sorted(DOC_DIR.iterdir()):
    if f.suffix.lower() == ".docx":
        size = f.stat().st_size
        info = extract_docx(f)
        rows.append(dict(
            fname=f.name, folder="lamdong_docs", ext="docx",
            size_kb=round(size/1024, 1), doc_type=guess_doc_type(f.name),
            is_signed=False, **info
        ))
    elif f.suffix.lower() == ".doc":
        size = f.stat().st_size
        info = extract_doc_binary(f)
        rows.append(dict(
            fname=f.name, folder="lamdong_docs", ext="doc",
            size_kb=round(size/1024, 1), doc_type=guess_doc_type(f.name),
            is_signed=False, **info
        ))

df = pd.DataFrame(rows)
df.to_csv(OUT_DIR / "eda_raw.csv", index=False, encoding="utf-8-sig")
print(f"✅ Đã xử lý {len(df)} file\n")

# ── Phát hiện cặp doc+signed_pdf ─────────────────────────────────────────
pdf_signed = set(
    f.replace("_Signed.pdf", "").replace("_signed.pdf", "")
    for f in df[df["is_signed"]]["fname"]
)
doc_stems = set(Path(f).stem for f in df[df["folder"] == "lamdong_docs"]["fname"])
duplicate_pairs = pdf_signed & doc_stems
print(f"🔁 Phát hiện {len(duplicate_pairs)} cặp DOC + Signed PDF (nội dung trùng)")

# ── Print thống kê tổng quan ──────────────────────────────────────────────
print("\n" + "="*60)
print("📊 TỔNG QUAN")
print("="*60)
print(f"  Tổng file          : {len(df)}")
print(f"  - PDF              : {(df.ext=='pdf').sum()}")
print(f"  - DOCX             : {(df.ext=='docx').sum()}")
print(f"  - DOC              : {(df.ext=='doc').sum()}")
print(f"  Tổng dung lượng    : {df.size_kb.sum()/1024:.1f} MB")
print(f"  PDF bị scan (OCR?) : {df.is_scanned.sum()} file  ({df.is_scanned.mean()*100:.1f}%)")
print(f"  File lỗi extract   : {df.error.notna().sum()}")
print(f"  File < {MIN_TEXT_CHARS} ký tự   : {(df.chars < MIN_TEXT_CHARS).sum()}")
print(f"  Cặp trùng doc/pdf  : {len(duplicate_pairs)}")

good = df[df.chars >= MIN_TEXT_CHARS]
print(f"\n📝 FILE CÓ TEXT ({len(good)} file):")
print(f"  Median words       : {good.words.median():.0f}")
print(f"  Median tokens      : {good.tokens.median():.0f}")
print(f"  Max tokens         : {good.tokens.max():.0f}  ({good.loc[good.tokens.idxmax(),'fname'][:50]})")
print(f"  Min tokens         : {good[good.tokens>0].tokens.min():.0f}")
print(f"  % > 2000 tokens    : {(good.tokens>2000).mean()*100:.1f}%   (cần chunking)")
print(f"  % > 8000 tokens    : {(good.tokens>8000).mean()*100:.1f}%   (cần chunking nhỏ)")

print(f"\n📂 THEO LOẠI VĂN BẢN (top 10):")
type_stats = (df.groupby("doc_type")
              .agg(so_luong=("fname","count"), tong_tokens=("tokens","sum"), median_tokens=("tokens","median"))
              .sort_values("so_luong", ascending=False)
              .head(10))
print(type_stats.to_string())

# ── Vẽ biểu đồ ───────────────────────────────────────────────────────────
sns.set_theme(style="whitegrid", font="DejaVu Sans")
fig, axes = plt.subplots(2, 3, figsize=(18, 10))
fig.suptitle("EDA – Lamdong Documents (RAG Preparation)", fontsize=14, fontweight="bold")

# 1. Phân phối token count
ax = axes[0, 0]
data = good.tokens.clip(upper=12000)
ax.hist(data, bins=50, color="#4C72B0", edgecolor="white")
ax.axvline(512, color="red",    lw=1.5, linestyle="--", label="512 (small chunk)")
ax.axvline(2000, color="orange",lw=1.5, linestyle="--", label="2000")
ax.axvline(8000, color="purple",lw=1.5, linestyle="--", label="8000 (context limit)")
ax.set_title("Phân phối Token Count")
ax.set_xlabel("Tokens (capped 12k)")
ax.set_ylabel("Số file")
ax.legend(fontsize=8)

# 2. File size KB distribution
ax = axes[0, 1]
ax.hist(df.size_kb.clip(upper=5000), bins=50, color="#55A868", edgecolor="white")
ax.set_title("Phân phối File Size (KB)")
ax.set_xlabel("KB (capped 5MB)")
ax.set_ylabel("Số file")

# 3. Doc type distribution
ax = axes[0, 2]
top_types = df.doc_type.value_counts().head(12)
colors = sns.color_palette("tab10", len(top_types))
bars = ax.barh(top_types.index[::-1], top_types.values[::-1], color=colors[::-1])
ax.set_title("Phân bổ Loại Văn Bản")
ax.set_xlabel("Số file")
for bar, val in zip(bars, top_types.values[::-1]):
    ax.text(bar.get_width()+1, bar.get_y()+bar.get_height()/2,
            str(val), va="center", fontsize=9)

# 4. PDF: text vs scanned
ax = axes[1, 0]
pdf_df = df[df.ext == "pdf"]
counts = [pdf_df.is_scanned.sum(), (~pdf_df.is_scanned).sum()]
labels = [f"Scan/ảnh\n({counts[0]})", f"Có text\n({counts[1]})"]
ax.pie(counts, labels=labels, autopct="%1.0f%%",
       colors=["#DD8452", "#4C72B0"], startangle=90)
ax.set_title("PDF: Text vs Scanned")

# 5. Ext breakdown
ax = axes[1, 1]
ext_counts = df.ext.value_counts()
ax.bar(ext_counts.index, ext_counts.values,
       color=["#4C72B0","#55A868","#DD8452"][:len(ext_counts)])
for i, (k, v) in enumerate(ext_counts.items()):
    ax.text(i, v+2, str(v), ha="center", fontweight="bold")
ax.set_title("Số file theo định dạng")
ax.set_ylabel("Số file")

# 6. Token bins (chunk strategy view)
ax = axes[1, 2]
bins = [0, 200, 512, 1000, 2000, 4000, 8000, float("inf")]
labels_b = ["0-200","200-512","512-1k","1k-2k","2k-4k","4k-8k","8k+"]
good["token_bin"] = pd.cut(good.tokens, bins=bins, labels=labels_b, right=False)
bin_counts = good.token_bin.value_counts().reindex(labels_b, fill_value=0)
colors_b = ["#aec6cf","#87ceeb","#4C72B0","#55A868","#FFC107","#FF7043","#e53935"]
ax.bar(labels_b, bin_counts.values, color=colors_b)
for i, v in enumerate(bin_counts.values):
    ax.text(i, v+0.5, str(v), ha="center", fontsize=9)
ax.set_title("Token Distribution (Chunk Strategy)")
ax.set_xlabel("Token range")
ax.set_ylabel("Số file")

plt.tight_layout()
plt.savefig(OUT_DIR / "eda_charts.png", dpi=150, bbox_inches="tight")
print(f"\n✅ Đã lưu biểu đồ → {OUT_DIR / 'eda_charts.png'}")

# ── Xuất báo cáo danh sách cần xử lý ─────────────────────────────────────
scanned_pdfs = df[df.is_scanned][["fname","size_kb","chars","pages"]]
scanned_pdfs.to_csv(OUT_DIR / "scanned_pdfs.csv", index=False, encoding="utf-8-sig")

empty_files = df[df.chars < MIN_TEXT_CHARS][["fname","folder","ext","chars","error"]]
empty_files.to_csv(OUT_DIR / "empty_or_error.csv", index=False, encoding="utf-8-sig")

dup_df = pd.DataFrame({"stem": sorted(duplicate_pairs)})
dup_df.to_csv(OUT_DIR / "duplicate_pairs.csv", index=False, encoding="utf-8-sig")

print(f"✅ Đã lưu: eda_raw.csv, scanned_pdfs.csv, empty_or_error.csv, duplicate_pairs.csv")
print(f"\n💡 GỢI Ý CHO RAG PIPELINE:")
pct_gt2k = (good.tokens > 2000).mean() * 100
print(f"   - {pct_gt2k:.0f}% file > 2000 tokens → cần chunk, gợi ý chunk_size=512, overlap=64")
print(f"   - {df.is_scanned.sum()} PDF scan → cần OCR (VietOCR / Tesseract tiếng Việt)")
print(f"   - {len(duplicate_pairs)} cặp trùng DOC+PDF → chỉ index 1 bản (ưu tiên PDF)")
print(f"   - {(df.chars<MIN_TEXT_CHARS).sum()} file rỗng/lỗi → loại khỏi index")
