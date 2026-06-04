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

```powershell
cd worker

# 1. Create venv + install deps (uv recommended; pip also works).
uv venv
.venv\Scripts\activate
uv pip install -e .

# 2. Copy env and edit.
copy .env.example .env
# Open .env and set WORKER_API_KEY to match the .NET app's AiWorker:ApiKey.

# 3. Start Qdrant locally (Docker).
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 `
  -v ${PWD}/qdrant_storage:/qdrant/storage qdrant/qdrant

# 4. Run the worker.
uvicorn app.main:app --reload --port 8000
```

The first request triggers a ~2 GB download of `BAAI/bge-m3` from
HuggingFace. Cached afterwards.

## Notes

- The tenant boundary (`department_id`) is enforced in the retriever during
  the query path (Phase 3) — on ingest we just **store** the tenant in
  every chunk's payload.
- Point id is `uuid5(NAMESPACE_OID, f"{document_id}:{chunk_index}")` so
  retries overwrite in place.
- All config comes from environment / `.env` — never commit secrets.
