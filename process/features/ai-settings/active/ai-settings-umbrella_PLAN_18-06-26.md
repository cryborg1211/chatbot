# AI Settings Dashboard — Program Plan (Umbrella)

Created: 2026-06-18
Status: ACTIVE — Phase 1 in progress (UI shell shipped)
Feature folder: `process/features/ai-settings/`

---

## Goal

Give admins a single page (`/admin/ai-settings`) to manage the AI backend of the LD3 chatbot: see live status, switch the active model, manage third‑party provider API keys (OpenAI / Anthropic / Gemini), and route queries between **local Ollama** and **cloud providers** — all admin‑only and audit‑logged.

This supersedes README "GIAI ĐOẠN 3" (Quản lý Model & Inference linh hoạt) and the handoff deferred item #1.

## Why it's a program, not a page

Research (2026-06-18) found the current worker is **Ollama-only**, the model is **fixed at boot** from `.env` (lru_cached settings + a constructed `Ollama(model=...)` singleton in `worker/app/main.py`), and `/health` only checks **liveness, not LLM connectivity**. So every mutation capability needs new Python worker endpoints plus a settings-persistence layer. Delivered in 4 shippable phases.

---

## Locked design decisions

1. **Worker stays stateless on config.** The .NET gateway passes `{provider, model, api_key}` in each `/api/query` request; the worker caches provider clients keyed by `(provider, model)`. This sidesteps the lru_cache / singleton-rebuild problem entirely — no live mutation of `app.state.llm`.
2. **Source of truth = a .NET DB table** (`AiConfig`), admin-editable, read per request (IOptions-style snapshot acceptable). Holds: active provider, per-provider model, advanced knobs (temperature, top_k, timeout, ollama base url).
3. **API keys encrypted at rest** via ASP.NET Core Data Protection (`IDataProtector`). Decrypted only to forward over the already-authenticated local worker hop. **Never** returned to the browser (write-only field; status shown as configured/validated + last-4 at most).
4. **Admin-only + audit-logged** throughout — reuse `AuthorizationPolicies.RequireAdmin` + `IAuditLogger` (new actions: `ai.config_change`, `ai.key_change`, `ai.provider_switch`).
5. **Providers in scope:** Ollama (local) + OpenAI + Anthropic + Gemini (user confirmed all three clouds, 2026-06-18).

---

## Phases

### Phase 1 — Status page (read-only)  ← in progress
Deliver the page + live observability. No mutation.
- **UI shell: DONE** (2026-06-18) — `Pages/Admin/AiSettings.cshtml(.cs)`, sidebar item in `_Layout`, light+dark. Currently static/mock.
- Remaining: new worker endpoint to report LLM status (ping Ollama `/api/tags`) + installed models; `IAiWorkerClient` method; `AiSettingsModel` calls it and renders **real** status cards + Ollama model list + connection badge. Worker-down → graceful "offline".
- Plan: `ai-settings-phase1-status_PLAN_18-06-26.md`

### Phase 2 — Settings store + live Ollama model switch
- `AiConfig` EF table + migration; admin can pick from installed Ollama models and switch the active model **without restart**.
- Worker reads `model` per-request (from `QueryRequest`) instead of the boot singleton; .NET passes the configured model on every `/api/query`.
- Wire the "Lưu thay đổi" + Ollama model dropdown + advanced knobs (temperature/top_k/timeout/base url) to the store.

### Phase 3 — Provider abstraction (worker, internal)
- Refactor `LlmRouter` → provider-agnostic factory/strategy: Ollama / OpenAI / Anthropic / Gemini, each with streaming `stream_chat`. Preserve `ENFORCED_SYSTEM_PROMPT` injection.
- Add provider SDKs (llama-index integrations or native). No UI change.

### Phase 4 — Keys + cloud/local routing + connectivity tests
- Encrypted key storage (Data Protection) + admin UI to enter/replace keys (write-only).
- Per-provider "Kiểm tra" → real validation (cheap provider call); status badges go live.
- Select active provider; `/api/query` forwards `{provider, model, api_key}`; worker routes accordingly.
- Audit + never echo keys.

---

## Sequencing & loop

Advance one phase at a time: research subagent/inline → execution approval → execute → validate → durable report in `process/features/ai-settings/reports/`. Each phase is independently shippable and leaves the app working.

## Blast radius (whole program)

- **.NET:** new `AiConfig` model + migration; `AiSettings` page; `IAiWorkerClient`/`AiWorkerClient` new methods; new admin API controller(s); Data Protection registration; `Program.cs` DI.
- **Worker (Python):** new status + (later) provider abstraction; `QueryRequest` schema gains `provider`/`model`/`api_key`; `llm_router.py` refactor; new endpoints under `/api`.
- **DB:** one new table (`AiConfig`) + possibly a keys table (or columns).
- **Config:** `appsettings.json` AiWorker section unchanged; keys move to DB (not appsettings/.env).

## Open questions / risks

- Key storage: single `AiConfig` row with encrypted columns vs a dedicated `AiProviderKey` table — decide in Phase 4.
- Per-request key transport: acceptable since .NET↔worker is local + authenticated; revisit if the worker is ever remoted.
- Ollama model list assumes Ollama reachable; handle empty/unreachable gracefully (Phase 1).
- "Toggle local Ollama" only meaningful once a cloud provider is configured (Phase 4).

## Status log

- 2026-06-18 — Program created. Research done. UI mockup approved by user. Phase 1 UI shell built (static). Phase 1 backend pending.
