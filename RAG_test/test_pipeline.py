"""Quick test: load + chunk a PDF and dump results for evaluation.

Usage:
    cd worker
    python -m RAG_test.test_pipeline
  OR from repo root:
    python RAG_test/test_pipeline.py
"""

from __future__ import annotations

import sys
import os
import time
from pathlib import Path

# Fix Windows console encoding for Vietnamese text
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add worker to path so we can import app modules
repo_root = Path(__file__).resolve().parent.parent
worker_dir = repo_root / "worker"
sys.path.insert(0, str(worker_dir))

def main():
    import psutil
    ram = psutil.virtual_memory()
    print(f"RAM: {ram.total / 1024**3:.1f}GB total, {ram.available / 1024**3:.1f}GB available")
    print()

    test_dir = repo_root / "RAG_test" / "doc"
    # Switch between test PDFs here:
    pdf_name = sys.argv[1] if len(sys.argv) > 1 else "665 boo coo 389 6 thang nam 2023.signed.signed.signed.pdf"
    pdf_path = test_dir / pdf_name

    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} not found")
        return

    print(f"Loading PDF: {pdf_path.name} ({pdf_path.stat().st_size / 1024**2:.1f}MB)")
    print("=" * 60)

    # --- Step 1: Load ---
    from app.services.loader import load_documents, LoaderError

    t0 = time.monotonic()
    try:
        docs = load_documents(pdf_path.read_bytes(), "application/pdf", pdf_path.name)
    except LoaderError as e:
        print(f"LOADER ERROR: {e}")
        return
    except Exception as e:
        print(f"CRASH: {type(e).__name__}: {e}")
        return
    load_time = time.monotonic() - t0

    doc = docs[0]
    md_text = doc.text if hasattr(doc, "text") else str(doc)
    print(f"\nLoad time: {load_time:.1f}s")
    print(f"Markdown length: {len(md_text)} chars")
    print(f"RAM after load: {psutil.virtual_memory().available / 1024**3:.1f}GB available")

    # Dump raw markdown
    md_out = repo_root / "RAG_test" / "index" / "raw_markdown.md"
    md_out.write_text(md_text, encoding="utf-8")
    print(f"Raw markdown saved: {md_out}")

    # --- Step 2: Chunk ---
    from app.services.chunker import Chunker

    chunker = Chunker(chunk_size=1024, chunk_overlap=250)

    t0 = time.monotonic()
    nodes = chunker.split(docs)
    chunk_time = time.monotonic() - t0

    print(f"\nChunk time: {chunk_time:.1f}s")
    print(f"Total chunks: {len(nodes)}")
    print(f"RAM after chunk: {psutil.virtual_memory().available / 1024**3:.1f}GB available")

    # --- Step 3: Evaluate chunks ---
    print("\n" + "=" * 60)
    print("CHUNK QUALITY EVALUATION")
    print("=" * 60)

    word_counts = []
    empty_chunks = 0
    tiny_chunks = 0      # < 20 words
    huge_chunks = 0      # > 1024 words (oversized)
    noise_chunks = 0     # signatures, headers, single-line junk

    noise_patterns = [
        "KT. GIÁM ĐỐC", "PHÓ GIÁM ĐỐC", "NGƯỜI LẬP BIỂU",
        "PHỤ LỤC", "Đơn vị tính", "Ban hành theo",
    ]

    for i, node in enumerate(nodes):
        text = node.get_content().strip()
        words = len(text.split())
        word_counts.append(words)

        if not text:
            empty_chunks += 1
        elif words < 20:
            tiny_chunks += 1
            is_noise = any(p in text for p in noise_patterns)
            if is_noise:
                noise_chunks += 1

        if words > 1024:
            huge_chunks += 1

    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0
    median_words = sorted(word_counts)[len(word_counts) // 2] if word_counts else 0

    print(f"Total chunks: {len(nodes)}")
    print(f"Avg words/chunk: {avg_words:.0f}")
    print(f"Median words/chunk: {median_words}")
    print(f"Min words: {min(word_counts) if word_counts else 0}")
    print(f"Max words: {max(word_counts) if word_counts else 0}")
    print(f"Empty chunks: {empty_chunks}")
    print(f"Tiny chunks (<20 words): {tiny_chunks}")
    print(f"Noise chunks (signatures/headers): {noise_chunks}")
    print(f"Oversized chunks (>1024 words): {huge_chunks}")

    # --- Step 4: Dump all chunks ---
    dump_path = repo_root / "RAG_test" / "index" / "chunk_dump.txt"
    with open(dump_path, "w", encoding="utf-8") as f:
        for i, node in enumerate(nodes):
            text = node.get_content()
            words = len(text.split())
            f.write(f"{'=' * 20} CHUNK {i} ({words} words) {'=' * 20}\n")
            f.write(text)
            f.write("\n\n")
    print(f"\nAll chunks dumped: {dump_path}")

    # --- Word count distribution ---
    buckets = {"0-10": 0, "11-50": 0, "51-200": 0, "201-500": 0, "501-1024": 0, "1024+": 0}
    for w in word_counts:
        if w <= 10: buckets["0-10"] += 1
        elif w <= 50: buckets["11-50"] += 1
        elif w <= 200: buckets["51-200"] += 1
        elif w <= 500: buckets["201-500"] += 1
        elif w <= 1024: buckets["501-1024"] += 1
        else: buckets["1024+"] += 1

    print("\nWord count distribution:")
    for bucket, count in buckets.items():
        bar = "█" * count
        print(f"  {bucket:>8}: {count:3d} {bar}")


if __name__ == "__main__":
    main()
