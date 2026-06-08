# LD3 RAG — Local Development Runbook (Windows / PowerShell)

> Daily startup ritual. Three terminals. ~1 minute to "ready" once everything is warm; ~3 minutes on a cold boot (bge-m3 + gemma4 first-load).

---

## 1. Prerequisites (one-time)

| Tool | How |
|---|---|
| Windows 10/11 (admin) | — |
| **Docker Desktop** | <https://docs.docker.com/desktop/install/windows-install/> |
| **Ollama for Windows** | <https://ollama.com/download/windows> · installs as a Windows service (no manual `ollama serve`) |
| **SQL Server Express 2017+** | local instance `DESKTOP-H5EP9KA\SQLEXPRESS` (mixed-mode auth disabled — using Windows auth via `Trusted_Connection=True`) |
| **.NET 10 SDK** | <https://dotnet.microsoft.com/download/dotnet/10.0> |
| **Python 3.11** | <https://www.python.org/downloads/release/python-3119/> |
| **Git** | any recent version |

### One-time data + model setup

```powershell
# Pull the LLM (gemma4:e2b ≈ 1.6 GB on first boot)
ollama pull gemma4:e2b

# Start Qdrant detached, with a persistent volume
docker run -d `
    --name ld3-qdrant `
    --restart unless-stopped `
    -p 6333:6333 -p 6334:6334 `
    -v ${PWD}\qdrant_storage:/qdrant/storage `
    qdrant/qdrant

# Python worker venv (run once, in the worker/ folder)
cd C:\Users\caokh\Desktop\vscode\chatbot\worker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
deactivate

# Apply EF Core migrations to SQL Express
cd C:\Users\caokh\Desktop\vscode\chatbot
dotnet ef database update
```

---

## 2. Pre-flight sanity check

Before the 3-terminal dance, verify the always-on services:

```powershell
# Qdrant container alive?
docker ps --filter "name=ld3-qdrant" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Ollama service alive + model present?
(Invoke-WebRequest http://localhost:11434/api/tags -UseBasicParsing).Content | ConvertFrom-Json | Select-Object -ExpandProperty models | Select-Object name

# SQL Express service alive?
Get-Service MSSQL`$SQLEXPRESS | Select-Object Status, Name

# Port collisions (should ALL return nothing)
Get-NetTCPConnection -State Listen -LocalPort 5101,8001,6333,11434 -ErrorAction SilentlyContinue
```

If any check fails → fix it before starting the worker / web below.

---

## 3. Daily startup — three terminals

> **Open three PowerShell windows.** Each command runs in the foreground; close = stop service.

### Terminal 1 — Qdrant (if not already detached)

Qdrant is started detached as a Docker container at install time and survives reboots (`--restart unless-stopped`). Use this terminal only if it's not running:

```powershell
docker start ld3-qdrant     # resume the existing container
# OR re-create if it was removed:
docker run -d --name ld3-qdrant --restart unless-stopped `
    -p 6333:6333 -p 6334:6334 `
    -v ${PWD}\qdrant_storage:/qdrant/storage qdrant/qdrant

# Tail logs to watch readiness:
docker logs -f ld3-qdrant
```

### Terminal 2 — Python AI worker (FastAPI + bge-m3 + LlamaIndex)

```powershell
cd C:\Users\caokh\Desktop\vscode\chatbot\worker
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8001 --reload
```

**Wait** until you see `INFO: Application startup complete.` (≈ 20–40 s on a cold boot — bge-m3 has to load into RAM). Do **not** start the .NET app before this — early requests will 502.

### Terminal 3 — .NET 10 Web (ASP.NET + Razor)

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

Close terminals 2 and 3 (`Ctrl+C`). Qdrant stays detached on purpose; stop it explicitly only if needed:

```powershell
docker stop ld3-qdrant
```

Persistent state:
- SQL data → `MSSQL$SQLEXPRESS` (Windows service)
- Vectors → `.\qdrant_storage\` on disk
- Uploaded files → `App_Data\uploads\`
- Logs → `App_Data\logs\`

Nothing is lost across reboots.

---

## 7. Troubleshooting cheatsheet

| Symptom | Likely cause | Fix |
|---|---|---|
| .NET log: `AiWorkerException: AI worker is unreachable` | Python worker not up yet, or stale `appsettings.json` BaseUrl | Wait for "Application startup complete" in T2; restart .NET if BaseUrl was changed mid-session |
| .NET log: `AI worker returned 404` on `/api/ingest` | Wrong port or path | Verify T2 shows `Uvicorn running on http://127.0.0.1:8000`; `appsettings.json` → `AiWorker:BaseUrl=http://localhost:8001/api` |
| Chat hangs forever on first token | gemma4 cold-loading on first request | First reply can take 30–60 s; subsequent chats are fast |
| Upload sits at "Đang vector hóa" forever | T2 crashed or Python venv missing deps | Check T2 console; re-run `pip install -e .` in `worker/` |
| `Path escapes storage root` on ingest | mixed `/` vs `\` in `Storage:UploadsRoot` | Already fixed in `LocalFileSystemStorage.cs`; ensure latest build |
| `ALTER TABLE` failed in `dotnet ef database update` | Old migration applied, then schema changed | Add a new migration: `dotnet ef migrations add <name>` then `database update` |
| Port 8001 already in use | another process bound to 8001 | `Get-NetTCPConnection -LocalPort 8001`, kill the owning PID |
| Ollama returns "model not found" | `gemma4:e2b` not pulled | `ollama pull gemma4:e2b` and verify with `ollama list` |

---

## 8. Useful commands during dev

```powershell
# Inspect Qdrant collection (point count, payload indexes)
(Invoke-WebRequest http://localhost:6333/collections/ld3_knowledge -UseBasicParsing).Content |
    ConvertFrom-Json | ConvertTo-Json -Depth 5

# Wipe Qdrant data (CAREFUL — irreversible for vectors only)
docker stop ld3-qdrant; Remove-Item -Recurse -Force .\qdrant_storage; docker start ld3-qdrant

# Tail .NET / Python logs to one stream (run in a 4th terminal if curious)
Get-Content .\App_Data\logs\app-*.log -Tail 50 -Wait

# Reset the database (CAREFUL — irreversible)
dotnet ef database drop --force
dotnet ef database update

# Tail Ollama service log (Windows)
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 50 -Wait
```

---

*Runbook covers local dev only. Production / air-gapped deployment is Phase 5 — see `.claude/PROJECT_MASTER_PLAN.md §9` + the Docker bundle.*
