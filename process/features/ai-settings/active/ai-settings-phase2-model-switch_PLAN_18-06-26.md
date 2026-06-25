# AI Settings — Phase 2: Settings store + live model switch

Created: 2026-06-18
Parent: `ai-settings-umbrella_PLAN_18-06-26.md`
Status: CODE-COMPLETE (2026-06-18) — migration applied; pending live restart test

---

## Objective

Admin switches the active Ollama model from the dashboard; chat uses it on the next message with no restart.

## Design (as built)

- **`AiConfig` singleton table** (Id = 1): `ActiveProvider`, `ActiveModel` (null = worker default), `Temperature`, `TopK`, `UpdatedAt`, `UpdatedBy`. Seeded all-null → behaviour unchanged until an admin saves.
- **`IAiConfigService` / `AiConfigService`**: get (find-or-create) + update.
- **`ChatService`** reads AiConfig per query (resilient `try/catch` → worker defaults on any error, so a missing table never breaks chat) and passes `Model`/`Temperature`/`TopK` in `QueryRequest`.
- **`.NET QueryRequest`** + worker **`schemas/query.py`** gain `model` / `temperature` / `top_k` (nullable; nulls omitted from the JSON body).
- **Worker `query.py._select_llm`**: reuse the boot singleton unless model/temperature differ, else build a cheap transient Ollama router (no singleton mutation, no model reload). `main.py` stashes `ollama_cfg` + `default_model` on `app.state`. Retrieval uses `req.top_k or state.retrieval_top_k`.
- **Page**: `OnGetAsync` loads effective model + status; `OnPostAsync` saves the selected model (PRG + success banner). A `<form method="post">` wraps the cards; only the Ollama `<select name="SelectedModel">` submits.

## Built / verified

- All of the above. Migration `20260618071444_AddAiConfig` created + **applied** (table + seed row present in the dev DB).
- `dotnet build` clean (real build via `dotnet ef`); worker schema + `_select_llm` verified; worker imports.

## Deferred (fast-follow / later phases)

- Advanced knobs UI (temperature / top_k / timeout / base_url) — disabled "Sắp có" in the view. The temp/top_k plumbing exists end-to-end but isn't wired to the form yet (temperature has a decimal-culture parse risk → wire deliberately, likely as a string parsed with `InvariantCulture`).
- Cloud provider selection + keys = Phases 3–4.

## Live test (after restart)

1. Restart the worker (`uvicorn app.main:app` on :8001) and the .NET app.
2. `/admin/ai-settings` → Ollama dropdown shows installed models, current one preselected.
3. Pick a different model → **Lưu thay đổi** → success banner + "Thay đổi cuối … bởi <admin>".
4. Send a chat message → answered by the newly-selected model (worker builds a transient router for it).
5. Fresh install (unchanged) → chat identical to before (null model → worker default).

## Notes

- No auto-migrate in `Program.cs`; `dotnet ef database update` was run this session. A teammate pulling this must run it too before starting the app (ChatService is resilient if they forget, but the settings page will error until the table exists).
- AiConfig is read per chat query (single-row, cheap); add `IMemoryCache` later if it ever matters.
