# LD3 RAG Worker

FastAPI worker that handles the AI side of the LD3 RAG chatbot:
parse uploaded documents, chunk + embed them with `BAAI/bge-m3`, upsert
into Qdrant. Called only by the .NET gateway (never by the browser).

## Endpoints

| Method | Path      | Auth                 | Purpose                                              |
| ------ | --------- | -------------------- | ---------------------------------------------------- |
| GET    | `/health` | none                 | Liveness probe.                                      |
| POST   | `/ingest` | `X-Worker-Api-Key`   | Multipart upload of one document. See master plan §2.6. |

## Quick start (Windows)

No Docker required — Qdrant and Redis both run as native Windows binaries.
This avoids Docker Desktop's WSL2 VM overhead (observed ~0.7GB+ RAM held by
`vmmem` just to run two lightweight containers), which matters on a
RAM-constrained laptop — see `_TABLE_RAM_THRESHOLD_GB` / OCR RAM gate in
`loader.py` and the reranker RAM guard in `reranker.py`.

```powershell
cd worker

# 1. Create venv + install deps (uv recommended; pip also works).
uv venv
.venv\Scripts\activate
uv pip install -e .

# 2. Copy env and edit.
copy .env.example .env
# Open .env and set WORKER_API_KEY to match the .NET app's AiWorker:ApiKey.

# 3. Download + run Qdrant natively (one-time setup).
#    Official Windows binary — pick the version matching worker's qdrant-client pin.
#    https://github.com/qdrant/qdrant/releases -> qdrant-x86_64-pc-windows-msvc.zip
#    Extract to e.g. ..\qdrant-native\, then:
..\qdrant-native\qdrant.exe

# 4. Download + run Redis natively (one-time setup).
#    Real open-source Redis for Windows (unofficial but MIT-licensed, no
#    install wizard, no license restrictions):
#    https://github.com/tporadowski/redis/releases -> Redis-x64-*.zip
#    Extract to e.g. ..\redis-native\, then:
..\redis-native\redis-server.exe ..\redis-native\redis.windows.conf

# 5. Run the worker.
uvicorn app.main:app --reload --port 8000
```

Both `qdrant.exe` and `redis-server.exe` just need to be running processes
bound to `localhost:6333` / `localhost:6379` — no service installation
required for local dev. Whoever verifies this on another machine (e.g. a
teammate checking the work) repeats steps 3-4 once; nothing else about the
setup changes versus the Docker version.

The first request triggers a ~2 GB download of `BAAI/bge-m3` from
HuggingFace. Cached afterwards.

## Notes

- The tenant boundary (`department_id`) is enforced in the retriever during
  the query path (Phase 3) — on ingest we just **store** the tenant in
  every chunk's payload.
- Point id is `uuid5(NAMESPACE_OID, f"{document_id}:{chunk_index}")` so
  retries overwrite in place.
- All config comes from environment / `.env` — never commit secrets.
