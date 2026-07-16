"""Re-create the Qdrant collection with the v2 schema and re-ingest the corpus.

Run AFTER the Phase 1 code changes are in place and Qdrant (1.10+) is up:
    cd worker
    python -m RAG_test.migrate_collection

What it does:
  1. Connects to Qdrant
  2. DELETES the existing collection (if any) — irreversible, but the corpus
     is only 4 docs and re-ingest is cheap. Confirms before deleting.
  3. Creates the v2 collection via VectorStore.ensure_collection()
     (named "dense" vector + text_segmented BM25 index)
  4. Re-ingests every file in RAG_test/doc/ via the full pipeline
     (load → chunk → embed → upsert with segmented text)
  5. Prints the per-document page manifest so you can verify Phase 0 routing

Requires: Qdrant running, bge-m3 model available (downloads ~2GB on first run).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

repo_root = Path(__file__).resolve().parent.parent
worker_dir = repo_root / "worker"
sys.path.insert(0, str(worker_dir))

DOC_DIR = repo_root / "RAG_test" / "doc"
EVAL_DEPARTMENT = "EVAL"


def main() -> None:
    from app.config import get_settings
    from app.services.chunker import Chunker
    from app.services.embedder import Embedder
    from app.services.loader import load_documents, LoaderError
    from app.services.vectorstore import VectorStore
    from app.services.chunk_metadata import prepend_document_context_to_chunks
    from qdrant_client import QdrantClient

    settings = get_settings()
    print(f"Qdrant: {settings.qdrant_url}  Collection: {settings.collection_name}")
    print(f"Docs to ingest: {sorted(p.name for p in DOC_DIR.glob('*'))}")
    print("=" * 70)

    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)

    # ---- 1. Delete existing collection (with confirmation) ----
    existing = {c.name for c in client.get_collections().collections}
    if settings.collection_name in existing:
        info = client.get_collection(settings.collection_name)
        print(f"Existing collection has {info.points_count} points.")
        if "--yes" not in sys.argv:
            resp = input(f"Delete and recreate '{settings.collection_name}'? [y/N] ")
            if resp.lower() not in ("y", "yes"):
                print("Aborted.")
                return
        print(f"Deleting collection '{settings.collection_name}'...")
        client.delete_collection(settings.collection_name)

    # ---- 2. Create v2 collection ----
    vs = VectorStore(client, settings.collection_name, settings.vector_size)
    vs.ensure_collection()
    print("v2 collection created (named 'dense' + text_segmented BM25 index).")

    # ---- 3. Init pipeline singletons ----
    print("Loading embedder (may download ~2GB on first run)...")
    embedder = Embedder(
        settings.embed_model,
        max_length=settings.embed_max_length,
        embed_batch_size=settings.embed_batch_size,
        torch_threads=settings.embed_torch_threads,
    )
    chunker = Chunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        token_cap=settings.embed_max_length,
    )

    # ---- 4. Re-ingest each doc ----
    for doc_path in sorted(DOC_DIR.iterdir()):
        if doc_path.is_dir():
            continue
        print(f"\n--- Ingesting: {doc_path.name} ---")
        try:
            file_bytes = doc_path.read_bytes()
            mime = {
                ".pdf": "application/pdf",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".doc": "application/msword",
                ".txt": "text/plain",
            }.get(doc_path.suffix.lower(), "application/octet-stream")

            documents = load_documents(file_bytes, mime, doc_path.name)
            nodes = chunker.split(documents)
            if not nodes:
                print(f"  SKIP: no chunks produced")
                continue

            raw_texts = [n.get_content() for n in nodes]
            texts = prepend_document_context_to_chunks(raw_texts, doc_path.name)
            vectors = embedder.encode(texts)

            document_id = str(uuid.uuid5(uuid.NAMESPACE_OID, doc_path.name))
            count = vs.upsert_chunks(
                document_id=document_id,
                department_id=EVAL_DEPARTMENT,
                original_name=doc_path.name,
                chunks=texts,
                vectors=vectors,
            )
            print(f"  chunks={count} document_id={document_id}")

            # Phase 0 manifest summary
            for d in documents:
                routes = getattr(d, "page_routes", []) or []
                if routes:
                    scanned = sum(1 for r in routes if r.get("ocr_used"))
                    dropped = sum(1 for r in routes if r.get("dropped"))
                    print(f"  pages={len(routes)} scanned={scanned} dropped={dropped}")
                    if dropped:
                        print(f"  ⚠ {dropped} page(s) dropped — holes in the index")
        except LoaderError as e:
            print(f"  PARSE ERROR: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {type(e).__name__}: {e}")

    client.close()
    print("\n" + "=" * 70)
    print("Re-ingest complete. Run the eval next:")
    print("  python -m RAG_test.eval_retrieval EVAL 5")


if __name__ == "__main__":
    main()
