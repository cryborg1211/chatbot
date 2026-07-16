# LD3 RAG — Local Development Runbook (Windows / PowerShell)

> Daily startup ritual. Four terminals. ~1 minute to "ready" once everything is warm; ~3 minutes on a cold boot (bge-m3 + qwen2.5:3b first-load).

---

## 1. Prerequisites (one-time)

| Tool | How |
|---|---|
| Windows 10/11 (admin) | — |
| **Ollama for Windows** | <https://ollama.com/download/windows> · installs as a Windows service (no manual `ollama serve`) |
| **SQL Server Express 2017+** | local instance `DESKTOP-H5EP9KA\SQLEXPRESS` (mixed-mode auth disabled — using Windows auth via `Trusted_Connection=True`) |
| **.NET 10 SDK** | <https://dotnet.microsoft.com/download/dotnet/10.0> |
| **Python 3.11** | <https://www.python.org/downloads/release/python-3119/> |
| **Git** | any recent version |

**No Docker.** Qdrant and Redis both run as native Windows binaries (migrated off
Docker Desktop — its WSL2 VM was holding ~0.7GB+ RAM just to run two lightweight
containers, RAM that matters on this laptop; see the OCR/reranker RAM gates below).

### One-time native binary setup

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot

# Qdrant — official Windows binary, must match the worker's qdrant-client pin
# (currently tested against server v1.18.2).
# https://github.com/qdrant/qdrant/releases -> qdrant-x86_64-pc-windows-msvc.zip
mkdir qdrant-native
# extract the downloaded zip into .\qdrant-native\ (yields qdrant.exe)

# Redis — real open-source Redis for Windows (unofficial fork, MIT license,
# no install wizard, no license restrictions). Only powers the legacy
# /api/documents/upload admin batch-ingest path (arq queue) — the primary
# .NET -> /api/ingest flow doesn't need it.
# https://github.com/tporadowski/redis/releases -> Redis-x64-*.zip
mkdir redis-native
# extract the downloaded zip into .\redis-native\ (yields redis-server.exe)
```

### One-time data + model setup

```powershell
# Pull the LLM (qwen2.5:3b — current default, config.py Settings.ollama_model)
ollama pull qwen2.5:3b

# Python worker venv (run once, in the worker/ folder)
cd C:\Users\caokh\Desktop\vscode\chatbot\worker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
deactivate

# Apply EF Core migrations to SQL Express
cd C:\Users\caokh\Desktop\vscode\chatbot
dotnet ef database update

# First-time Qdrant collection: start qdrant.exe (see §3 Terminal 1), then
# populate the v3 schema (dense + bm25 sparse vector) with the sample corpus:
cd worker
$env:PYTHONPATH = "C:\Users\caokh\Desktop\vscode\chatbot"
.venv\Scripts\python.exe -m RAG_test.migrate_collection --yes
```

---

## 2. Pre-flight sanity check

Before the 4-terminal dance, verify the always-on services:

```powershell
# Qdrant reachable + correct version?
(Invoke-WebRequest http://localhost:6333 -UseBasicParsing).Content | ConvertFrom-Json

# Redis reachable?
.\redis-native\redis-cli.exe PING     # expect: PONG

# Ollama service alive + model present?
(Invoke-WebRequest http://localhost:11434/api/tags -UseBasicParsing).Content | ConvertFrom-Json | Select-Object -ExpandProperty models | Select-Object name

# SQL Express service alive?
Get-Service MSSQL`$SQLEXPRESS | Select-Object Status, Name

# Port collisions (should ALL return nothing before starting anything)
Get-NetTCPConnection -State Listen -LocalPort 5101,8001,6333,6379,11434 -ErrorAction SilentlyContinue
```

If any check fails → fix it before starting the worker / web below.

---

## 3. Daily startup — four terminals

> **Open four PowerShell windows.** Each command runs in the foreground; close = stop service.

### Terminal 1 — Qdrant (native)

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot\qdrant-native
.\qdrant.exe
```

Wait for the log line confirming the REST API is listening on `6333`. Data persists
under `qdrant-native\storage\` on disk (no separate volume flag needed — the
binary defaults to a `storage/` folder next to itself).

### Terminal 2 — Redis (native)

Only needed if you use the admin multi-file upload (`/api/documents/upload`).
The main .NET → `/api/ingest` chat/document flow does not depend on Redis.

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot\redis-native
.\redis-server.exe .\redis.windows.conf
```

### Terminal 3 — Python AI worker (FastAPI + bge-m3 + LlamaIndex)

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot\worker
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8001 --reload
```

**Wait** until you see `INFO: Application startup complete.` (≈ 20–40 s on a cold
boot — bge-m3 has to load into RAM). Do **not** start the .NET app before this —
early requests will 502.

### Terminal 4 — .NET 10 Web (ASP.NET + Razor)

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot
dotnet watch --urls http://localhost:5101 --environment Development
```

Hot-reload is on; .cshtml / .cs / .js edits trigger an auto-rebuild.

---

## 4. Access

| Surface | URL |
|---|---|
| Login | <http://localhost:5101/Account/Login> |
| Register | <http://localhost:5101/Account/Register> (dept codes: `IT`, `HR`, `ADMIN`) |
| Chat | <http://localhost:5101/Chat> |
| Document manager | <http://localhost:5101/Admin/Documents> |
| Worker OpenAPI | <http://localhost:8001/docs> |
| Worker health | <http://localhost:8001/health> |
| Qdrant dashboard | <http://localhost:6333/dashboard> |

---

## 5. Smoke test (~30 s)

```powershell
# Worker reachable?
(Invoke-WebRequest http://localhost:8001/health -UseBasicParsing).Content

# Routes registered?
(Invoke-WebRequest http://localhost:8001/openapi.json -UseBasicParsing).Content |
    ConvertFrom-Json | Select-Object -ExpandProperty paths

# .NET reachable?
(Invoke-WebRequest http://localhost:5101/Account/Login -UseBasicParsing).StatusCode
```

Then in the browser: login → upload a small PDF on **Quản lý tài liệu** → wait for `Đã sẵn sàng` (60 s typical) → return to **Chat** → ask a question → watch tokens stream (thinking block dims, answer below).

---

## 6. Shutdown

Close terminals 1–4 (`Ctrl+C` in each). Nothing runs detached by default anymore
(no Docker daemon holding services alive in the background) — if you want Qdrant/
Redis to persist across a terminal close, run them with
`Start-Process -WindowStyle Hidden` instead of foreground.

Persistent state:
- SQL data → `MSSQL$SQLEXPRESS` (Windows service)
- Vectors → `.\qdrant-native\storage\` on disk
- Redis job-queue data → in-memory only, disposable (job results TTL 1h, `arq` `keep_result=3600`) — nothing to back up
- Uploaded files → `App_Data\uploads\`
- Logs → `App_Data\logs\`

Nothing is lost across reboots except the ephemeral Redis queue state (by design).

---

## 7. Troubleshooting cheatsheet

| Symptom | Likely cause | Fix |
|---|---|---|
| .NET log: `AiWorkerException: AI worker is unreachable` | Python worker not up yet, or stale `appsettings.json` BaseUrl | Wait for "Application startup complete" in T3; restart .NET if BaseUrl was changed mid-session |
| .NET log: `AI worker returned 404` on `/api/ingest` | Wrong port or path | Verify T3 shows `Uvicorn running on http://127.0.0.1:8001`; `appsettings.json` → `AiWorker:BaseUrl=http://localhost:8001/api` |
| Chat hangs forever on first token | qwen2.5:3b cold-loading on first request | First reply can take 30–60 s; subsequent chats are fast |
| Upload sits at "Đang vector hóa" forever | T3 crashed, Python venv missing deps, or Qdrant/Redis not running | Check T3 console; re-run `pip install -e .` in `worker/`; confirm T1/T2 are up |
| `Path escapes storage root` on ingest | mixed `/` vs `\` in `Storage:UploadsRoot` | Already fixed in `LocalFileSystemStorage.cs`; ensure latest build |
| `ALTER TABLE` failed in `dotnet ef database update` | Old migration applied, then schema changed | Add a new migration: `dotnet ef migrations add <name>` then `database update` |
| Port 8001 already in use | another process bound to 8001 | `Get-NetTCPConnection -LocalPort 8001`, kill the owning PID |
| Ollama returns "model not found" | `qwen2.5:3b` not pulled | `ollama pull qwen2.5:3b` and verify with `ollama list` |
| Scanned PDF ingest fails: "cần OCR ... Hiện không đủ bộ nhớ (RAM: X.XGB, cần ≥4GB)" | Real RAM constraint — OCR (EasyOCR + TableFormer) genuinely needs ~4-5GB free, not overly conservative | Close other apps (browser tabs, IDEs) and retry; check free RAM with `Get-CimInstance Win32_OperatingSystem \| Select FreePhysicalMemory` |
| Log line `reranker_skipped_low_ram available=X.XGB need=3.0GB` | Reranker (bge-reranker-v2-m3, ~2GB) skipped — not a bug, RAM guard working as designed | Retrieval still works (falls back to RRF order); free RAM if you want reranking active |
| Worker log `Warning: unauthenticated requests to HF Hub` on first sparse/dense model load | Cosmetic — HuggingFace rate-limit warning, not an error | Ignore; set `HF_TOKEN` env var only if you hit actual rate limits |
| `qdrant.exe` window closes immediately | Port 6333 already in use (old container/process still bound) | `Get-NetTCPConnection -LocalPort 6333`, kill the owning PID, retry |

---

## 8. Useful commands during dev

```powershell
# Inspect Qdrant collection (point count, payload indexes, sparse/dense vector config)
(Invoke-WebRequest http://localhost:6333/collections/ld3_knowledge -UseBasicParsing).Content |
    ConvertFrom-Json | ConvertTo-Json -Depth 5

# Wipe Qdrant data (CAREFUL — irreversible for vectors only)
# Close the Terminal 1 qdrant.exe window first, then:
Remove-Item -Recurse -Force .\qdrant-native\storage
# Restart qdrant.exe (T1), then re-ingest:
cd worker; $env:PYTHONPATH = "C:\Users\caokh\Desktop\vscode\chatbot"
.venv\Scripts\python.exe -m RAG_test.migrate_collection --yes

# Re-run the retrieval eval (recall@k / MRR / nDCG — dense vs hybrid vs hybrid+reranker)
cd worker; $env:PYTHONPATH = "C:\Users\caokh\Desktop\vscode\chatbot"
.venv\Scripts\python.exe -m RAG_test.eval_retrieval EVAL 5

# Full worker test suite (no live Qdrant/Redis needed — all mocked)
cd worker
.venv\Scripts\python.exe -m pytest app/services/ -v

# Tail .NET / Python logs to one stream (run in a 5th terminal if curious)
Get-Content .\App_Data\logs\app-*.log -Tail 50 -Wait

# Reset the database (CAREFUL — irreversible)
dotnet ef database drop --force
dotnet ef database update

# Tail Ollama service log (Windows)
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 50 -Wait
```

---

*Runbook covers local dev only. Production / air-gapped deployment is Phase 5 — see `.claude/PROJECT_MASTER_PLAN.md §9`. Note the local-dev Docker removal above does not necessarily apply to that production plan; revisit its infra choice separately.*
