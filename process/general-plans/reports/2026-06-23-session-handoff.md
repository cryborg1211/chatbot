# Session Handoff — 2026-06-23

Branch: `main` (all work uncommitted)
Last commit: `feaf8b6` — "config ai model dashboard UX/UI i guess?"

---

## What Was Done (All Uncommitted)

### AI Settings Dashboard — 4-Phase Program (ALL PHASES CODE-COMPLETE)

Full multi-provider admin dashboard at `/admin/ai-settings`. Umbrella plan: `process/features/ai-settings/active/ai-settings-umbrella_PLAN_18-06-26.md`.

**Phase 1 — Status page (read-only)**
- `Pages/Admin/AiSettings.cshtml` + `.cshtml.cs` — async page: shell renders from DB config only, JS fetches `?handler=Status` for live worker data
- Worker endpoint `GET /api/llm/status` (`worker/app/api/llm.py`) — pings Ollama `/api/tags`, returns installed models + reachability
- `Infrastructure/AiWorker/AiWorkerClient.cs` — `GetLlmStatusAsync()` method
- New DTOs: `Contracts/LlmStatus.cs`

**Phase 2 — Settings store + model switch**
- `Models/AiConfig.cs` — singleton table (Id=1), fields: ActiveProvider, ActiveModel, Temperature, TopK, UpdatedAt, UpdatedBy
- `Services/Ai/AiConfigService.cs` + interface — get/update singleton config
- Migration `20260618071444_AddAiConfig` — created + applied (table + seed row)
- `Services/Chat/ChatService.cs` — reads AiConfig per query, passes provider/model/apikey/temperature/topk to worker
- `Infrastructure/AiWorker/Contracts/QueryRequest.cs` — extended with Provider, Model, ApiKey, Temperature, TopK
- `worker/app/schemas/query.py` — matching optional fields
- `worker/app/api/query.py` — `_select_llm()` routes per request (Ollama reuses singleton; cloud builds fresh)

**Phase 3 — Provider abstraction (worker internal)**
- `worker/app/services/llm_router.py` — `SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "gemini")`, `build_chat_llm()` factory with lazy imports, per-request routing (no singleton mutation)
- `worker/pyproject.toml` — added `llama-index-llms-openai`
- `worker/app/api/llm.py` — `POST /llm/test` (auth-only connectivity check per provider, GET /v1/models), `POST /llm/models` (live model list with text-only filter `_is_text_model()`)

**Phase 4 — Encrypted keys + cloud routing**
- `Models/AiProviderKey.cs` — PK=Provider, EncryptedKey (Data Protection blob), timestamps
- `Services/Ai/ProviderKeyService.cs` + interface — encrypt/decrypt/save/delete keys, never echo plaintext to browser
- Migration `20260619015808_AddAiProviderKey` — file exists, **may not be applied to DB yet** (see blockers)
- `Program.cs` — registered `IAiConfigService`, `IProviderKeyService`, `AddDataProtection()`
- `Pages/Admin/AiSettings.cshtml.cs` — AJAX handlers: `OnGetStatusAsync`, `OnPostSaveKeyAsync`, `OnPostModelsAsync`, `OnPostAsync` (PRG save)
- UI: provider rows with radio select, model dropdowns, combined "Đồng bộ & kiểm tra" button, API key inputs (write-only, data-eye toggle), save bar with PRG + success banner

### PDF Enhancement
- `worker/app/services/loader.py` — PDF pipeline with:
  - EasyOCR (Vietnamese `vi` + English `en`) for scanned pages
  - TableFormer ACCURATE mode for complex table parsing
  - `_pdf_converter()` cached via `@lru_cache`
  - Quality gate `_assess_pdf_quality()` — rejects blurry/bad scans with Vietnamese error messages
  - Thresholds: MIN_CHARS_PER_PAGE=50, MIN_WORDS_PER_PAGE=12, MIN_ALPHA_RATIO=0.30, MAX_REPLACEMENT_RATIO=0.03

### Event Loop Fix (Correctness)
- `worker/app/api/ingest.py` — pipeline moved to `_run_pipeline()`, called via `asyncio.to_thread`
- `worker/app/api/query.py` — embed + retrieve moved to `asyncio.to_thread`
- Prevents slow PDF parse / embed from blocking concurrent `/api/query` requests

### Documents Sidebar
- `Pages/Admin/Documents.cshtml` — converted from standalone to use `_Layout`, sidebar persists
- `Pages/Shared/_Layout.cshtml` — role-gated sidebar links (admin-only items hidden from regular users)

### CPU Throttle Revert
- `worker/app/runtime_limits.py` — DELETED
- All imports/calls to `runtime_limits` removed from `main.py` and `queue_worker.py`
- Hardcoded `do_ocr=True` and `TableFormerMode.ACCURATE` in `loader.py`
- Worker runs at full power (8 cores, no throttle)

### Other UI Work (from prior session, already committed in feaf8b6 and earlier)
- Register link cleanup, document download endpoint, admin notification bell
- Chat logo/subtitle removal, dark-mode button fix
- Avatar storage refactor (IDocumentStorage + auth'd endpoint)
- Admin page dedup (consolidated with _Layout)
- Graceful avatar fallback (icon underneath, img overlay with onerror)

---

## BLOCKERS — Do These First

### 1. Apply AiProviderKey Migration (CRITICAL)
Migration file exists on disk but may not be applied to DB. Without it, key saving fails at runtime.

```powershell
# Stop .NET app first, then:
dotnet ef database update
```

If it errors "already applied", that's fine — migration was applied in a prior session. If it says "table already exists", the migration was partially applied; check `__EFMigrationsHistory`.

### 2. Commit the Massive Uncommitted Batch (~60 changed/new files)
Everything above is uncommitted. Suggested commit strategy:

```powershell
# Option A: One big commit (simple)
git add -A
git commit -m "feat: AI settings dashboard (4 phases), PDF OCR pipeline, event loop fix, documents sidebar, throttle revert"

# Option B: Split by feature (cleaner history)
# 1. AI Settings (Models, Services, Pages, Infrastructure, Migrations, worker endpoints)
# 2. PDF pipeline (loader.py, pyproject.toml)
# 3. Event loop fix (ingest.py, query.py)
# 4. Documents sidebar (_Layout, Documents.cshtml)
# 5. Process/harness files (CLAUDE.md, AGENTS.md, protocols, plans)
```

---

## Cleanup Tasks (Not Blocking, Do When Convenient)

### Dead Code Removal
- `OnPostTestAsync` handler in `Pages/Admin/AiSettings.cshtml.cs` (~20 lines) — replaced by combined sync+test via `/llm/models`
- `POST /llm/test` endpoint in `worker/app/api/llm.py` — same reason
- `TestLlmAsync` in `Infrastructure/AiWorker/AiWorkerClient.cs` + `IAiWorkerClient.cs` — same reason
- `Infrastructure/AiWorker/Contracts/LlmTestResult.cs` — unused DTO

### Missing Provider SDKs
Only `llama-index-llms-openai` installed. Still need:
- `llama-index-llms-anthropic`
- `llama-index-llms-gemini`

Install when ready to test those providers:
```bash
cd worker && pip install llama-index-llms-anthropic llama-index-llms-gemini
```
Then add to `pyproject.toml` dependencies.

### Advanced Knobs UI
Temperature / TopK sliders on AI Settings page currently disabled ("Sắp có"). Backend supports them — `AiConfig` has the fields, `ChatService` reads them, `QueryRequest` carries them. Just need to wire the UI controls.

### Context Doc Update
`process/context/all-context.md` needs update for:
- Provider routing architecture (per-request model/provider selection)
- PDF pipeline (OCR + TableFormer + quality gate)
- Async page pattern (shell render + JS status fetch)
- New services (AiConfigService, ProviderKeyService)
- New worker endpoints (/llm/status, /llm/test, /llm/models)
- Event loop fix pattern (asyncio.to_thread)

### Superseded Plan
`process/general-plans/active/admin-unified-page_PLAN_10-06-26.md` — admin Index page was consolidated, plan is stale. Move to `completed/` or delete.

---

## Architecture Quick Reference

### SSE Chat Pipeline (with provider routing)
```
Browser → ChatController (SSE) → ChatService
  → reads AiConfig (provider/model)
  → decrypts cloud API key if cloud provider
  → AiWorkerClient.QueryAsync(provider, model, apiKey, temperature, topK)
  → Python /api/query
    → _select_llm(state, req): Ollama=reuse singleton; Cloud=fresh LlmRouter
    → embed (asyncio.to_thread) → retrieve (asyncio.to_thread) → prompt → stream
```

### AI Settings Page (async pattern)
```
Page load → OnGetAsync: reads AiConfig only (fast, no worker call)
JS onload → fetch(?handler=Status): hits worker /llm/status, returns JSON
JS renders: connection badge, model dropdowns, provider status
Save → OnPostAsync: PRG pattern, updates AiConfig
Key save → OnPostSaveKeyAsync: AJAX, encrypts + stores via Data Protection
Sync+Test → OnPostModelsAsync: AJAX, calls worker /llm/models per provider
```

### Worker LLM Factory
```python
# worker/app/services/llm_router.py
SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "gemini")
build_chat_llm(provider, model, *, api_key, base_url, timeout, temperature)
# Lazy imports per provider. Returns LlamaIndex ChatLLM instance.
# LlmRouter wraps this + enforced system prompt + stream_chat.
```

### Key Security
- Encrypted at rest via ASP.NET Core Data Protection (`IDataProtector("ai-provider-keys.v1")`)
- Decrypted only to forward over authenticated local worker hop
- Never returned to browser (write-only UI field)
- `ProviderKeyService.GetPlaintextAsync()` only called by `ChatService` per query

---

## Known Issues (Low Priority)

- `code-review-graph` pre-commit hook fails with `UnicodeEncodeError` (cp1252 codec) on Windows with Vietnamese text. Non-blocking — commit still succeeds.
- Bash shell in Claude Code session is broken (unbalanced quote at init line 185 from caveman hook). Use PowerShell for all commands.
- Non-text models sometimes leak through the filter (e.g., Google image-gen models). The `_NON_TEXT_MARKERS` blacklist in `worker/app/api/llm.py` catches most but may need expansion.

---

## File Inventory (Changed/New, Uncommitted)

### C# (.NET) — Modified
- `Infrastructure/AiWorker/AiWorkerClient.cs` — +GetLlmStatusAsync, +TestLlmAsync, +GetProviderModelsAsync
- `Infrastructure/AiWorker/Contracts/QueryRequest.cs` — +Provider, Model, ApiKey, Temperature, TopK
- `Infrastructure/AiWorker/IAiWorkerClient.cs` — +3 interface methods
- `Migrations/ApplicationDbContextModelSnapshot.cs` — +AiConfig, +AiProviderKey tables
- `Pages/Admin/AiSettings.cshtml` — full rewrite (async page, provider UI)
- `Pages/Admin/AiSettings.cshtml.cs` — full rewrite (AJAX handlers, PRG save)
- `Pages/Admin/Documents.cshtml` — converted to _Layout
- `Pages/Shared/_Layout.cshtml` — +role-gated sidebar, +admin nav items
- `Program.cs` — +IAiConfigService, +IProviderKeyService, +AddDataProtection
- `Services/Chat/ChatService.cs` — +AiConfig read, +provider/model/key forwarding

### C# (.NET) — New
- `Infrastructure/AiWorker/Contracts/LlmModelsResult.cs`
- `Infrastructure/AiWorker/Contracts/LlmTestResult.cs` (dead code — remove)
- `Migrations/20260618071444_AddAiConfig.cs` + `.Designer.cs`
- `Migrations/20260619015808_AddAiProviderKey.cs` + `.Designer.cs`
- `Models/AiConfig.cs`
- `Models/AiProviderKey.cs`
- `Services/Ai/AiConfigService.cs` + `IAiConfigService.cs`
- `Services/Ai/ProviderKeyService.cs` + `IProviderKeyService.cs`

### Python (Worker) — Modified
- `worker/app/api/ingest.py` — asyncio.to_thread pipeline
- `worker/app/api/query.py` — asyncio.to_thread embed/retrieve, _select_llm per-request routing
- `worker/app/main.py` — +llm router, +ollama_cfg stash, +/api/llm route
- `worker/app/schemas/query.py` — +provider, model, api_key, temperature, top_k fields
- `worker/app/services/llm_router.py` — provider-agnostic factory, build_chat_llm, SUPPORTED_PROVIDERS
- `worker/app/services/loader.py` — PDF OCR pipeline, quality gate, EasyOCR Vietnamese
- `worker/pyproject.toml` — +easyocr, +llama-index-llms-openai

### Python (Worker) — New
- `worker/app/api/llm.py` — /llm/status, /llm/test, /llm/models endpoints

### Python (Worker) — Deleted
- `worker/app/runtime_limits.py` (throttle revert)

### Process/Harness — Modified/New
- `CLAUDE.md`, `AGENTS.md` — harness updates
- `process/development-protocols/*` — protocol updates (orchestration, phase-programs, etc.)
- `process/features/ai-settings/active/*` — umbrella + 4 phase plans
- Various `process/_seeds/*` files

---

## Caveman Mode

User prefers **caveman mode** (terse, no filler, fragments OK, code/commits normal). Activate with `/caveman full` or similar. Drop articles, filler, pleasantries, hedging. Pattern: `[thing] [action] [reason]. [next step].`
