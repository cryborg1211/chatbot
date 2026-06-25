# AI Settings — Phase 3: Provider abstraction (worker)

Created: 2026-06-18
Parent: `ai-settings-umbrella_PLAN_18-06-26.md`
Status: CODE-COMPLETE (2026-06-18) — internal refactor, no behaviour change

---

## Objective

Make the worker LLM layer provider-agnostic so OpenAI / Anthropic / Gemini can plug in alongside Ollama. Internal only — no UI, no .NET change.

## Built (one file: `worker/app/services/llm_router.py`)

- `build_chat_llm(provider, model, *, api_key, base_url, timeout, temperature)` — factory returning a llama-index streaming LLM. Provider SDKs imported **lazily** (`ollama` / `openai` / `anthropic` / `gemini`), so an uninstalled cloud provider never breaks the default path. Unknown provider → `ValueError`.
- `SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "gemini")`.
- `LlmRouter` refactored to build its backend via the factory + new `provider` / `api_key` params + a `provider` property. Signature is **back-compatible**: existing keyword calls `LlmRouter(base_url=…, model=…, timeout=…, temperature=…)` still work (provider defaults to `ollama`), so `main.py` and `query.py._select_llm` are unchanged.
- `ENFORCED_SYSTEM_PROMPT` + `stream_chat` + `_inject_system_prompt` untouched.

## Verified

- Import OK; back-compat `LlmRouter(base_url=…, model=…)` builds (provider=ollama).
- `build_chat_llm('foo', …)` → `ValueError`.
- `build_chat_llm('openai', …)` → lazy `ModuleNotFoundError` (package not installed) — expected; worker boots fine Ollama-only.

## Deferred → Phase 4

- Add cloud SDKs to `pyproject` + `pip install` (`llama-index-llms-openai` / `-anthropic` / `-gemini`).
- Thread `provider` + `api_key` per request: `.NET QueryRequest` + worker `schemas/query.py` + `_select_llm`, sourced from `AiConfig` (+ encrypted keys).
- Connectivity tests per provider; UI key management + provider switch.

## Notes

- No behaviour change: every path still resolves to Ollama with the default keyword calls. Cloud is structurally ready but inert until Phase 4.
