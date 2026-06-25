# AI Settings — Phase 4: Keys + cloud routing + tests + async page

Created: 2026-06-18
Parent: `ai-settings-umbrella_PLAN_18-06-26.md`
Status: CODE-COMPLETE (2026-06-18) — pending `AiProviderKey` migration (app must be stopped) + live test

---

## Built

### Async page (no blocking on worker)
- `OnGetAsync` loads only `AiConfig` (fast) → renders shell.
- `OnGetStatusAsync` (`?handler=Status`) returns JSON (worker/Ollama reachability, installed models, active provider/model, configured-key set). Page JS fetches it and fills the DOM, so a slow/offline worker never blocks page load.

### Encrypted keys
- `AiProviderKey` table (Provider PK, EncryptedKey, UpdatedAt, ValidatedAt). `ProviderKeyService` uses ASP.NET Core Data Protection (`AddDataProtection`, protector `ai-provider-keys.v1`). Keys encrypted at rest, **never** returned to the browser.
- `OnPostSaveKeyAsync` (AJAX) encrypts + stores; audit `ai.key_change`.

### Cloud/local routing
- `QueryRequest` (.NET + worker) gain `provider` + `api_key`. `ChatService` reads `AiConfig.ActiveProvider`, decrypts the cloud key only here, forwards over the authenticated worker hop. Worker `query.py._select_llm` routes by provider (Ollama reuses the boot singleton; cloud builds a fresh router with the per-request key). `AiConfigService.UpdateAsync(provider, model, updatedBy)`.

### Connectivity test
- Worker `POST /api/llm/test` {provider, model, api_key} → `build_chat_llm` + tiny `acomplete("ping")` → `{ok, error?}`, never raises. `.NET` `IAiWorkerClient.TestLlmAsync` + `OnPostTestAsync` (AJAX); marks the key validated on success.

### Providers
- Phase 3 factory already supports ollama/openai/anthropic/gemini (lazy). `llama-index-llms-openai` added to `pyproject` + **installed**. Anthropic/Gemini: install their SDK when needed.

## Verified
- `dotnet build` clean (isolated). Worker import OK; `/llm/status` + `/llm/test` routes present; `QueryRequest` accepts provider/api_key; `build_chat_llm('openai', …)` constructs an `OpenAI` LLM.

## REMAINING (blocked on running app)
1. **Stop the .NET app**, then: `dotnet ef migrations add AddAiProviderKey` + `dotnet ef database update` (creates the `AiProviderKeys` table). `--no-build` produces an empty migration (stale running-app assembly) — must be a real build with the app stopped.
2. Restart worker + app.

## Live test (after migration + restart)
- `/admin/ai-settings` loads instantly (shell); status cards + Ollama models populate a moment later via AJAX.
- Save an OpenAI key → badge flips to "Đã cấu hình"; the OpenAI row becomes selectable.
- "Kiểm tra" on OpenAI → real validation (green Hoạt động / red Lỗi).
- Select OpenAI + a model → Lưu thay đổi → a chat message is answered by OpenAI. Switch back to Ollama → local again.

## Safety
- Default stays Ollama (no key) → chat unchanged. `ChatService` resilient to config/key read failures. Keys encrypted at rest, never echoed.

## Deferred
- Anthropic/Gemini SDK install. Advanced knobs UI (temp/top_k). Key delete UI. Per-provider model lists from the provider API (currently static labels).
