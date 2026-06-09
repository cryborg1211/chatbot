# LD3 AI Chatbot - All Tests

Last updated: 2026-06-09

Attach this file first when the task involves testing, verification, or test debugging.

This is the fast operator guide for the testing surface:

- which runner to use
- what command to start with
- how to quickly debug common failures
- which deeper file to read next

Do not load the whole `process/context/tests/` folder by default. Start here, then drill down.

---

## How This File Works

This is the `all-tests.md` entrypoint for the `tests/` context group. It follows the `all-*.md` routing convention:

1. Agents read `all-context.md` first and get routed here for testing tasks
2. This file gives quick decision rules and commands
3. For deeper details, agents follow the routing table below to specific docs

As the project grows, add deeper docs to this group (e.g., `e2e-tests.md`, `debugging-and-pitfalls.md`) and add routing entries below. This file stays the fast-start entrypoint.

---

## What This Covers

- test runner selection
- quick commands by stack
- fast debugging procedures
- current testing gaps worth remembering

## Read This When

Use this file when you need to:

- run tests after implementation
- decide between test runners
- debug failing tests
- verify document parsing works correctly

## Quick Routing

(No deeper test docs yet. Add routing entries here as they are created.)

## Quick Decision Guide

### C# (.NET) backend — no test project yet

There is currently no xUnit/NUnit/MSTest project set up for the .NET backend. When tests are added:

- Likely runner: `dotnet test` with xUnit
- Scope: controller logic, service logic, EF Core integration tests
- Key testable surfaces: `ChatService`, `DocumentService`, `AiWorkerClient` SSE parsing

### Python worker — manual file-type tests only

Testing is early-stage. Manual testing has been done with sample `.doc` and `.docx` files from `data/lamdong_docs/`. No pytest suite exists yet.

When pytest is added:

- Runner: `pytest` from within `worker/`
- Key testable surfaces:
  - `loader.py` — document parsing (Docling, legacy .doc conversion)
  - `chunker.py` — chunk splitting (current SentenceSplitter, future MarkdownNodeParser)
  - `retriever.py` — tenant-filtered vector search
  - `llm_router.py` — system prompt injection, message merging
  - `prompt_builder.py` — Jinja2 template rendering
  - `embedder.py` — embedding batch encode (mock model for unit tests)

## Default Verification Order

Unless the task clearly needs a different path:

1. run the narrowest existing automated test
2. use unit/integration tests before browser tests
3. use end-to-end tests only when the real UI is the thing being verified
4. **For document parsing changes:** test with sample files from `data/lamdong_docs/` and `data/lamdong_pdf/`

## Commands

### .NET Backend

```bash
# Build (verify compilation)
dotnet build

# Run the backend (starts on default Kestrel port)
dotnet run

# Run EF Core migrations
dotnet ef database update

# Add a new migration
dotnet ef migrations add <MigrationName>
```

### Python Worker

```bash
# Run the worker (from project root)
cd worker && uvicorn app.main:app --reload --port 8001

# Install dependencies (from worker/)
pip install -e .

# Future pytest (when added)
cd worker && pytest
cd worker && pytest -v app/services/test_chunker.py  # single file
```

### Full Stack (both services needed for chat)

```bash
# Terminal 1: .NET backend
dotnet run

# Terminal 2: Python worker
cd worker && uvicorn app.main:app --reload --port 8001

# Terminal 3: Redis (for arq queue, needed for document upload)
redis-server

# Also required running: Qdrant (port 6333), Ollama (port 11434), SQL Server
```

## Debugging Quick Reference

- **Docling slow on first run:** bge-m3 model (~2 GB) downloads on first FastAPI startup. Takes ~30s to load each restart.
- **LibreOffice needed for .doc files:** Legacy `.doc` parsing requires `soffice` (LibreOffice) installed and on PATH.
- **Redis optional:** The arq queue is optional — worker still serves `/health`, `/api/ingest`, `/api/query` without it. Only `/api/documents/upload` (async job queue) is disabled.
- **SQL Server connection:** Uses Windows auth (`Trusted_Connection=True`). Connection string in `appsettings.json`.
- **Worker API key:** Both the .NET backend (`AiWorker:ApiKey`) and Python worker (`WORKER_API_KEY`) must share the same key value.

## Known Gaps

- No automated test suite for either the .NET backend or Python worker
- No pytest configuration or test directory structure in `worker/`
- No xUnit/NUnit project for C# backend
- No integration tests for the SSE streaming pipeline (ChatController → AiWorkerClient → Python /api/query)
- No load/performance testing
- No CI/CD pipeline to run tests automatically
- Sample documents in `data/` are used for manual testing only
- PDF parsing not yet tested (Docling supports it, but focus has been on .doc/.docx)
