# AI Settings — Phase 1: Status Page (read-only)

Created: 2026-06-18
Parent: `ai-settings-umbrella_PLAN_18-06-26.md`
Status: ACTIVE — UI shell done, backend pending
Complexity: SIMPLE–MEDIUM

---

## Objective

Make the AI Settings page show **real, live** status (no mutation): which model is active, whether the worker + Ollama are reachable, and the list of installed Ollama models. Everything else on the page stays mock until later phases.

## Done already (2026-06-18)

- `Pages/Admin/AiSettings.cshtml` + `AiSettings.cshtml.cs` — full static shell, light+dark, admin-only (`RequireAdmin`), route `/admin/ai-settings`.
- Sidebar item "Cấu hình AI" in `Pages/Shared/_Layout.cshtml` (`ActiveNav == "AiSettings"`).
- JS mock interactions (provider select, key show/hide, temperature, "Kiểm tra").

## Remaining work

### 1. Worker: LLM status endpoint
New authenticated endpoint, e.g. `GET /api/llm/status` in `worker/app/api/` (new `llm.py` router, included in `main.py` under `/api`, guarded by `Depends(require_api_key)`).
Returns JSON (snake_case):
```
{
  "provider": "ollama",
  "active_model": "<settings.ollama_model>",
  "base_url": "<settings.ollama_base_url>",
  "ollama_reachable": true,
  "installed_models": ["qwen2.5:3b", "gemma2:2b", ...]
}
```
- `ollama_reachable` + `installed_models` come from a short-timeout GET to `{ollama_base_url}/api/tags` (httpx). On failure: `ollama_reachable=false`, `installed_models=[]` (never 500 the status call).
- Read current model/base_url from `get_settings()`.

### 2. .NET client method
- `IAiWorkerClient.GetLlmStatusAsync(CancellationToken)` → new `LlmStatus` contract record in `Infrastructure/AiWorker/Contracts/`.
- `AiWorkerClient` impl: GET `llm/status`, parse with existing `JsonOpts` (snake_case). On transport failure, **do not throw to the page** — return a sentinel (e.g. `LlmStatus` with `WorkerReachable=false`) or let the page catch `AiWorkerException`.

### 3. Page model wiring
- `AiSettingsModel.OnGetAsync(CancellationToken)` calls the client; expose properties: `WorkerReachable`, `OllamaReachable`, `ActiveModel`, `BaseUrl`, `InstalledModels`.
- Catch `AiWorkerException` → `WorkerReachable=false`, render an "offline" state (status cards show "Không kết nối" / amber).

### 4. View: replace the mocked status bits with real data
- Status cards: active model (`@Model.ActiveModel`), Ollama connection (green "Kết nối" / amber "Không kết nối" from `@Model.OllamaReachable`), worker state.
- Ollama provider row: populate the `<select>` from `@Model.InstalledModels`, pre-select `@Model.ActiveModel`.
- **Leave mock:** cloud provider rows (OpenAI/Anthropic/Gemini), API-key inputs, "Khóa API 1/3", advanced inputs, Save bar, "Kiểm tra" buttons — all Phase 2-4.

## Touchpoints

| File | Change |
|------|--------|
| `worker/app/api/llm.py` | NEW — status endpoint |
| `worker/app/main.py` | include the new router under `/api` |
| `Infrastructure/AiWorker/Contracts/` | NEW `LlmStatus` record |
| `Infrastructure/AiWorker/IAiWorkerClient.cs` | + `GetLlmStatusAsync` |
| `Infrastructure/AiWorker/AiWorkerClient.cs` | impl GET `llm/status` |
| `Pages/Admin/AiSettings.cshtml.cs` | `OnGetAsync` + status props |
| `Pages/Admin/AiSettings.cshtml` | bind real status + Ollama models |

## Out of scope (later phases)

Model switching/persistence (P2), provider abstraction (P3), keys + cloud routing + real connectivity tests (P4). No DB changes in Phase 1. No `QueryRequest` changes in Phase 1.

## Verification

- `dotnet build` clean.
- Worker + Ollama up → page shows real active model + installed model list + green "Kết nối".
- Stop Ollama → page shows amber "Không kết nối", empty model list, no 500.
- Stop worker → page renders "offline" gracefully (no exception leaks to the user).
- Non-admin → blocked by `RequireAdmin`.
- Light + dark both correct.
- Update code-review-graph after edits.

## Resume handoff

If interrupted: the UI shell + nav are committed-ready; backend wiring is the remaining work. Start at "Remaining work §1" (worker endpoint), then §2 → §3 → §4. The worker must be restartable to test (`uvicorn app.main:app`), and `.NET` app restarted (Razor build-time compiled). Note the dev worker runs on `http://localhost:8001/api` per `appsettings.json`.
