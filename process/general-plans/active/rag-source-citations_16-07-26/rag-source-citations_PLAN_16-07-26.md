---
name: plan:rag-source-citations
description: "Implementation plan for Gemini-style source citations UI (right-side drawer + inline chips + persistence + legacy bug fix)"
date: 16-07-26
feature: general-plans
---

# PLAN — Gemini-Style Source Citations UI

**Date:** 16-07-26
**Complexity:** COMPLEX (single plan file, sequential checklist sections — NOT a phase program; INNOVATE's own signal score was 4/7 with no S4 phase-program signal)
**SPEC:** `process/general-plans/active/rag-source-citations_16-07-26/rag-source-citations_SPEC_16-07-26.md`
**Status strip:** ⏳ PLANNED (all 4 layers)

## Overview

Add a Gemini-style "sources panel" (right-side drawer, desktop-only) to every RAG answer, plus
best-effort inline `[n]` citation chips that link into it, on BOTH the live SSE chat stream and the
Razor-rendered conversation-history reload. Folds in a persistence-layer schema change (new
`SourcesJson` column on `ChatMessage`) that also fixes a pre-existing bug: reloaded conversations
today show the generic "Tài liệu" label instead of the real document title because the writer only
ever persisted bare document-id strings. A stretch Layer 3 (full-chunk fetch-by-id proxy endpoint)
is designed in full but recommended for a fast-follow pass, not this EXECUTE.

## Quick Links

- [Locked Decisions From INNOVATE](#locked-decisions-from-innovate-carried-forward-verbatim)
- [Architecture / Design Decisions](#architecture--design-decisions)
- [Touchpoints](#touchpoints)
- [Public Contracts](#public-contracts)
- [Blast Radius](#blast-radius)
- [Implementation Checklist](#implementation-checklist)
- [Test Coverage Plan](#test-coverage-plan-vc-test-coverage-plan-discipline)
- [Verification Evidence](#verification-evidence)
- [Risk Predictions](#risk-predictions)
- [Test Infra Improvement Notes](#test-infra-improvement-notes)
- [Resume and Execution Handoff](#resume-and-execution-handoff)
- [Validate Contract](#validate-contract)

---

## Phase Completion Rules

A layer is NOT complete until:

1. **Integration Test** — Works with other system pieces end-to-end (live stream AND reload path).
2. **Manual Test** — A human can perform the action in a real browser session.
3. **Data Verification** — DB row / persisted JSON confirmed correct by direct inspection.
4. **Error Handling** — Failure/edge cases (empty sources, legacy rows, malformed markers) degrade gracefully.
5. **User Confirmation** — User visually confirms it works (screenshot or described behavior).

Status meanings: ⏳ PLANNED · 🔨 CODE DONE (written, not E2E tested) · 🧪 TESTING · ✅ VERIFIED (tested
AND confirmed) · 🚧 BLOCKED. **Never mark ✅ VERIFIED on "build succeeds" or "no compile errors"
alone.**

---

## Context and Goals

Officials cannot currently see which document/passage grounded an AI answer, and reloaded
conversations show a broken generic "Tài liệu" label instead of the real title. This plan
implements the SPEC's 4 layers (schema+bug-fix, drawer panel, inline chips, stretch full-chunk
view) so officials can verify AI answers against source documents in seconds. Full requirements,
acceptance criteria, and constraints are locked in the SPEC — this plan does not restate them, it
maps them to exact files and steps.

## Locked Decisions From INNOVATE (carried forward verbatim — do not re-litigate)

- **Q2 panel placement:** Right-side drawer, desktop-first, no mobile/responsive layout.
- **Q1 grouping:** Flat sectioned list inside the drawer — non-collapsible document sub-headers,
  chunks always visible. Every chunk entry has a stable `data-source-idx` anchor.
- **Q3 chip parity:** Ship `[n]` chips on BOTH chat.js (live) and Razor (history) via a
  SINGLE-PASS regex re-scan of the COMPLETE answer text — at the SSE `done` event on the JS side,
  server-side over the already-complete `ChatMessage.Content` on the Razor side. No incremental
  token-by-token buffering; this sidesteps the split-marker problem entirely.
- **Q4 Layer 3 shape:** New .NET controller endpoint → `IAiWorkerClient` → new Python worker
  endpoint, mirroring the `DocumentsController.Delete` → `IAiWorkerClient.DeleteDocumentAsync`
  shape. Direct browser→worker calls are rejected (breaks tenant/auth model). **CRITICAL:** the
  chunk-fetch-by-id endpoint MUST include an explicit post-fetch `department_id` check
  server-side — Qdrant's `client.retrieve(ids=[...])` cannot combine id-lookup with a payload
  filter in one call. This is a named checklist item AND a VALIDATE gate (see
  [Layer 3 checklist](#layer-3--full-chunk-view-proxy-endpoint-stretch--recommended-fast-follow)).

## Architecture / Design Decisions

These are judgment calls made during PLAN so EXECUTE has zero ambiguity:

1. **Index convention — 1-based, uniform, everywhere.** The prompt template
   `worker/app/services/prompts/rag_system.j2` line 8 uses Jinja's `{{ loop.index }}`, which is
   1-indexed (confirmed by reading the template). So: `[1]` = first entry in the SSE `sources`
   array (0-based array index 0). The SAME 1-based number is used for the `data-source-idx`
   attribute on BOTH the drawer's chunk-entry cards (the click TARGET) and the inline citation chip
   spans (the click TRIGGER). One number, one meaning, everywhere — no off-by-one translation
   layer anywhere in the code.

2. **One shared drawer instance, hydrated per message.** Rather than one drawer per message
   bubble, there is exactly ONE `<aside id="sources-drawer">` in the DOM (static markup added once
   in `Pages/Chat/Index.cshtml`). Every message's "Nguồn tham khảo (N)" trigger button and every
   inline chip click calls `openSourcesDrawer(sources)` with THAT message's own source array,
   which clears and rebuilds the shared drawer body. This means clicking sources/chips on two
   different messages always shows the right message's sources, and avoids N duplicate drawer DOM
   trees in a long conversation.

3. **JSON-into-`<script>` embedding is SAFE here because of `System.Text.Json`'s default
   encoder — this is a deliberate, verified security decision, not an oversight.**
   `System.Text.Json.JsonSerializer.Serialize(...)` with **default options** (no custom
   `JavaScriptEncoder`) already HTML/JS-escapes `<`, `>`, `&`, and `'` as `\uXXXX` sequences. This
   is the standard, documented-safe .NET pattern for embedding server JSON into a page for JS to
   read (used because it's the only path from C# object → client JS runtime without a second round
   trip). Rule for EXECUTE: **never pass a custom `JsonSerializerOptions` with a relaxed/None
   encoder when serializing for the script-tag sidecar** — that would reopen the injection risk.
   The DB-column persistence serialization (Layer 4) is a *separate*, independently-configured
   `JsonSerializer.Serialize` call and MAY use default (Pascal-case) options since it never touches
   HTML — do not conflate the two call sites.
   - The payload for the script-tag sidecar (Razor → JS transport) uses
     `JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase }` so JS receives
     `{idx, documentId, title, snippet, score}` directly — no field-name adapter needed on the
     history/reload path. The live SSE path builds the same camelCase-shaped JS object directly in
     chat.js from the wire's snake_case fields (`document_id` → `documentId`, etc.) via a small
     inline `.map()`, since it already has the parsed JS objects in memory (no serialization
     round-trip, so no encoder question applies there).
   - The embedding MUST be inside a `<script type="application/json" data-role="sources-payload">`
     element BODY (never inside an HTML attribute value) — `Html.Raw` is only safe for the
     `<script>` body case given the encoder guarantee above; it is NOT safe to `Html.Raw` the same
     JSON string into a quoted HTML attribute (a literal `'` or `"` inside a title could still break
     attribute-value parsing depending on quote choice, even after JS-string escaping, because HTML
     attribute-value escaping rules differ from JS-string escaping rules). This plan does not use
     `Html.Raw` in any attribute context — only in the two `<script type="application/json">` sidecars.

4. **Marker regex is `\[(\d+)\]`, applied identically in JS and C#.** Digits only, no cap on digit
   count (so `[123]` parses to `n=123`, then range-checked against `sourceCount` and rendered as
   plain text if out of range — this is how AC6 "out-of-range" degrades). This regex will also
   match a coincidental bracketed number in prose that happens to be ≤ `sourceCount` (e.g. a legal
   article number "mục [15]"). This is an ACCEPTED SPEC-level limitation — Layer 2 is explicitly
   "display-only, best-effort" per SPEC's Out of Scope list ("Editing or correcting AI-generated
   citation markers"). Do not add heuristics to disambiguate; not in scope.

5. **Legacy vs rich data is a first-class boolean, not inferred ad hoc.** `BuildSourcePanel`
   (Layer 4/Razor) returns `(Entries, HasRichData)`. Chips are rendered ONLY when
   `HasRichData == true`. This is what implements AC12's "without inline chips" requirement for
   legacy rows precisely — passing `sourceCount = 0` into `SplitAnswerIntoSegments` for a legacy
   message forces every `[n]` marker in old messages to render as inert plain text.

---

## Touchpoints

**C# — Layer 4 (schema + persistence + bug fix)**
- `Models/ChatMessage.cs` — add `SourcesJson` property
- `data/ApplicationDbContext.cs` (`ConfigureChatMessage`, lines ~158-183) — fluent config for new column
- `Migrations/` — new migration file (EF-generated) + `ApplicationDbContextModelSnapshot.cs` update (EF-generated)
- `Services/Chat/ChatService.cs` — `StreamReplyAsync` (lines 114-216) + `PersistAssistantMessageAsync` (lines 243-300)

**C# — Layer 1 + Layer 2 (drawer + chips, both render paths)**
- `Pages/Chat/Index.cshtml.cs` — replace `ParseSourceCites`/`SourceCite` with `BuildSourcePanel`/`SourcePanelEntry`; add `SplitAnswerIntoSegments`/`AnswerSegment`
- `Pages/Chat/Index.cshtml` — assistant-message block rewrite (chip-rendered content, sources-trigger button + JSON sidecar); shared drawer markup added once near end of `<body>`

**JS — Layer 1 + Layer 2 (live path)**
- `wwwroot/js/chat.js` — `appendAssistantBubble` template change; `onSources`/`onDone` handler changes; new functions: `openSourcesDrawer`, `wireCitations`, `renderCitationChips`, `splitAnswerIntoSegments`, `highlightSource`, `showDrawer`/`hideDrawer`, `initHistoryCitations` (history-path wiring, runs once on load)

**Python / C# — Layer 3 (stretch, documented not executed this pass)**
- `worker/app/services/vectorstore.py` — new `get_chunk_by_id` method
- `worker/app/api/documents.py` — new `GET /api/documents/chunk/{chunk_id}` route
- `worker/app/services/test_chunk_fetch_tenant.py` — new pytest file (deferred)
- `Infrastructure/AiWorker/IAiWorkerClient.cs` + `AiWorkerClient.cs` — new method (deferred)
- new/extended controller action, tenant-scoped like `DocumentsController` (deferred)

## Public Contracts

- **New DB column** `ChatMessages.SourcesJson` (nvarchar(max), nullable) — internal persistence
  only; never queried directly by the browser, only read server-side by `IndexModel`.
- **SSE wire contract unchanged.** `/api/query`'s `sources` event still excludes the `text` field
  (confirmed: `worker/app/api/query.py` line 128, `exclude={"text"}`) — Layer 1/2 build entirely
  from the existing `{id, document_id, title, snippet, score}` shape, per SPEC's "no wire format
  change without cause" constraint.
- **No change** to `/api/chat/send` response headers or `IChatService` interface — all Layer 4
  changes are inside `ChatService`'s private `PersistAssistantMessageAsync`, confirmed not part of
  `IChatService.cs`.
- **New internal UI DOM contract** (not a network API): `data-source-idx` attribute, `.citation-chip`
  class, `.sources-trigger` class, `<script type="application/json" data-role="sources-payload">`
  sidecar — these are conventions internal to this feature, documented here for EXECUTE and future
  maintainers, not exposed externally.
- **Deferred Layer 3** would introduce `GET /api/documents/chunk/{chunk_id}?department_id=...`
  (worker) and a mirrored .NET controller action — documented in the Layer 3 checklist, NOT part of
  this EXECUTE pass's public surface.

## Blast Radius

- **Files touched this pass:** 5 C# files modified (`ChatMessage.cs`, `ApplicationDbContext.cs`,
  `ChatService.cs`, `Index.cshtml.cs`, `Index.cshtml`) + 1 new EF migration file (+ 1 auto-updated
  snapshot) + 1 JS file modified (`chat.js`) = **~8 touched/created files**.
- **Packages/runtimes:** single app (.NET) + browser JS. Python worker is NOT touched this pass
  (Layer 3 deferred) — `worker/` stays untouched in this EXECUTE.
- **Risk classes present:** schema/migration (Layer 4 — minimum Hybrid test tier required, see Test
  Coverage Plan). No auth, billing, or public-API-contract changes in this pass. No new
  dependencies, agents, or runtime surfaces.
- **Signal score context (from INNOVATE):** 4/7 — schema surface touched (S2) + 5+ files in blast
  radius (S7) + user requested depth for the citation-chip design (S5) + (borderline) 3+ distinct
  approaches were compared during INNOVATE (S3). Below phase-program threshold; single plan file
  is correct per INNOVATE's own assessment and the complex-decision table ("one main execution
  stream, even if long" → standard complex).

---

## Execution Brief

### Layer 4 — Schema + persistence + bug fix
**What happens:** Add `SourcesJson` column, migrate, capture the full per-chunk source list at
persist time, fix the reload title-loss bug by reading titles from the new column with a safe
legacy fallback.
**Test:** `dotnet build` + `dotnet ef database update` apply cleanly; send a live query, inspect
the `ChatMessages` row's `SourcesJson` value directly in SQL Server; reload the conversation and
confirm real titles appear (not "Tài liệu").
**Verify:** `SELECT TOP 1 SourcesJson, SourceDocumentIdsJson FROM ChatMessages WHERE Role = 1 ORDER BY CreatedAt DESC` — both columns populated correctly, `SourceDocumentIdsJson` unchanged in shape/width.
**Done when:** A brand-new assistant message persists both columns correctly, and reload shows the
real title for that message.

### Layer 1 — Source drawer
**What happens:** Right-side drawer, flat sectioned list grouped by document, per-chunk
snippet/score/download-link, wired identically from live SSE data and from the persisted column.
**Test:** Send a question with known source docs — confirm the "Nguồn tham khảo (N)" button appears
with the correct distinct-document count; open the drawer, confirm grouping and no "%" near the
score; send a non-retrieval prompt (e.g. "Xin chào") and confirm no button/error appears; reload the
page and confirm the drawer content is identical to the live version for the same message.
**Verify:** Visual inspection + `data-source-idx` attribute present on every chunk card.
**Done when:** Drawer opens/closes correctly, download link opens the existing tenant-scoped
download flow, zero-sources case shows nothing.

### Layer 2 — Inline citation chips
**What happens:** `[n]` markers in the answer become clickable chips (both live and history paths),
mapped 1:1 to drawer entries by position, degrading safely on malformed/out-of-range/zero markers.
**Test:** Use a cloud provider (e.g. Gemini) known to emit `[n]` markers — confirm chips render,
click opens/scrolls the drawer to the right entry; force an out-of-range marker (`[9]` with 4
sources) — confirm inert plain text; use the local Ollama model (no markers) — confirm Layer 1 still
works standalone; reload a legacy (pre-feature) conversation — confirm no chips render and no crash.
**Verify:** `data-source-idx` on chip spans matches the drawer's card indices exactly.
**Done when:** All AC5-AC9 scenarios pass manually in-browser.

### Layer 3 — Full-chunk view proxy endpoint (stretch, recommended fast-follow)
**What happens:** (If scheduled) new worker endpoint + .NET proxy to fetch one full chunk's text by
id, with a mandatory tenant check.
**Test:** (When scheduled) pytest with a crafted cross-tenant chunk id, following
`test_retriever_fusion.py`'s tenant-filter-test pattern — confirms department mismatch returns 404,
never the chunk text.
**Verify:** (When scheduled) code review confirms the tenant check runs on every code path, not just
the happy path.
**Done when:** (When scheduled) cross-tenant fetch is provably blocked before this is considered done.

### Expected Outcome
- Every RAG answer with ≥1 retrieved source shows a "Nguồn tham khảo (N)" trigger button.
- Clicking it (or a `[n]` chip) opens a shared right-side drawer with grouped, snippet-level source
  detail and a working download link.
- Reloaded conversations show the same information, including real document titles for messages
  created after this ships; legacy messages degrade gracefully.
- Layer 3 is fully designed but deferred; no chunk-fetch-by-id surface ships this pass.

---

## Implementation Checklist

### Layer 4 — Schema + persistence + bug fix (no dependencies — do first)

1. **`Models/ChatMessage.cs`** — add:
   ```csharp
   /// <summary>
   /// Full per-chunk source list (JSON array of the SSE `sources` event's
   /// `SourceDocument` shape, in original SSE order) that grounded an
   /// assistant reply. Nullable — null for legacy rows (pre-feature) and for
   /// user rows. Does NOT replace <see cref="SourceDocumentIdsJson"/>, which
   /// stays untouched for backward compat.
   /// </summary>
   public string? SourcesJson { get; set; }
   ```
   No `[MaxLength]` attribute — matches the existing `Content` property's pattern (nvarchar(max)
   configured only via fluent API, never a data annotation cap).

2. **`data/ApplicationDbContext.cs`** — inside `ConfigureChatMessage` (after the existing
   `entity.Property(m => m.SourceDocumentIdsJson).HasMaxLength(4000);` line), add:
   ```csharp
   entity.Property(m => m.SourcesJson).HasColumnType("nvarchar(max)");
   ```
   Do not touch the `SourceDocumentIdsJson` line — AC13 requires it stays byte-for-byte unchanged.

3. **New EF migration** — run:
   ```bash
   dotnet ef migrations add AddChatMessageSourcesJson
   ```
   Confirm the generated `Up()` contains exactly one `AddColumn<string>(name: "SourcesJson", table:
   "ChatMessages", type: "nvarchar(max)", nullable: true)` and `Down()` contains exactly one
   `DropColumn(name: "SourcesJson", table: "ChatMessages")` — mirrors
   `Migrations/20260616072428_AddUserAvatarPath.cs`'s single-`AddColumn` shape exactly, adapted for
   `nvarchar(max)` (no `maxLength:` arg, since `max` has none). Do not let EF emit any change to the
   `SourceDocumentIdsJson` column in this migration — if the generated diff touches it, something is
   wrong upstream (stop and investigate before applying).
   Run `dotnet ef database update` and confirm it applies cleanly (AC13's Fully-Automated gate).

4. **`Services/Chat/ChatService.cs`** — `StreamReplyAsync` (around line 176):
   - Add `IReadOnlyList<SourceDocument> allSources = Array.Empty<SourceDocument>();` alongside the
     existing `var sourceDocumentIds = new HashSet<string>();` accumulator.
   - In the `case QueryEvent.Sources s:` branch, in addition to the existing `foreach (var doc in
     s.Documents)` loop that populates `sourceDocumentIds`, add `allSources = s.Documents;` (single
     assignment — the docstring on `QueryEvent` confirms exactly one `Sources` event per stream, so
     this is not an accumulation, just a capture).
   - Pass `allSources` through to `PersistAssistantMessageAsync` as a new parameter.

5. **`Services/Chat/ChatService.cs`** — `PersistAssistantMessageAsync` (around line 243):
   - Add parameter `IReadOnlyList<SourceDocument> allSources`.
   - Add to the `ChatMessage` object construction:
     ```csharp
     SourcesJson = allSources.Count == 0
                       ? null
                       : JsonSerializer.Serialize(allSources),
     ```
   - Do NOT touch the existing `SourceDocumentIdsJson = ...` line — keep it exactly as-is (AC13).
   - `JsonSerializer.Serialize(allSources)` uses DEFAULT options (Pascal-case: `Id`, `DocumentId`,
     `Title`, `Snippet`, `Score`) — this is a pure internal DB round-trip, not HTML-facing, so the
     default encoder question from Architecture Decision 3 does not apply here.

6. **`Pages/Chat/Index.cshtml.cs`** — replace `ParseSourceCites`/`SourceCite` with:
   ```csharp
   public sealed record SourcePanelEntry(
       int Index,           // 1-based — see Architecture Decision 1
       string DocumentId,
       string Title,
       string? Snippet,     // null for legacy fallback entries
       double? Score);      // null for legacy fallback entries

   public static (IReadOnlyList<SourcePanelEntry> Entries, bool HasRichData) BuildSourcePanel(
       string? sourcesJson, string? legacySourceIdsJson)
   {
       if (!string.IsNullOrWhiteSpace(sourcesJson))
       {
           try
           {
               var docs = JsonSerializer.Deserialize<List<SourceDocument>>(sourcesJson);
               if (docs is { Count: > 0 })
               {
                   var entries = docs.Select((d, i) => new SourcePanelEntry(
                       i + 1, d.DocumentId, d.Title, d.Snippet, d.Score)).ToList();
                   return (entries, true);
               }
               return (Array.Empty<SourcePanelEntry>(), true); // rich data, but 0 sources — AC3
           }
           catch { /* fall through to legacy path below on any malformed JSON */ }
       }

       // Legacy fallback — reuse exactly the parsing behavior of today's ParseSourceCites
       // (tolerates both the dead-code {id,title} object shape and bare id-string arrays),
       // but the returned entries have Snippet=null, Score=null so callers know not to render chips.
       if (string.IsNullOrWhiteSpace(legacySourceIdsJson))
           return (Array.Empty<SourcePanelEntry>(), false);

       try
       {
           using var doc = JsonDocument.Parse(legacySourceIdsJson);
           if (doc.RootElement.ValueKind != JsonValueKind.Array)
               return (Array.Empty<SourcePanelEntry>(), false);

           var result = new List<SourcePanelEntry>();
           int idx = 1;
           foreach (var el in doc.RootElement.EnumerateArray())
           {
               if (el.ValueKind == JsonValueKind.Object)
               {
                   var id    = el.TryGetProperty("id",    out var idEl)    ? idEl.GetString()    ?? "" : "";
                   var title = el.TryGetProperty("title", out var titleEl) ? titleEl.GetString() ?? "Tài liệu" : "Tài liệu";
                   result.Add(new SourcePanelEntry(idx++, id, title, null, null));
               }
               else if (el.ValueKind == JsonValueKind.String)
               {
                   result.Add(new SourcePanelEntry(idx++, el.GetString() ?? "", "Tài liệu", null, null));
               }
           }
           return (result, false);
       }
       catch { return (Array.Empty<SourcePanelEntry>(), false); }
   }
   ```
   Add `using chatbot.Infrastructure.AiWorker.Contracts;` for `SourceDocument`. This satisfies AC11
   (real titles from `SourcesJson`), AC12 (legacy rows degrade gracefully, `HasRichData=false` so no
   chips), and never throws (mirrors the existing try/catch discipline).

### Layer 1 — Source drawer

7. **`Pages/Chat/Index.cshtml`** — add the shared drawer markup ONCE, near the end of `<body>`
   (after the `<footer>`, before `<script src="~/js/chat.js">`):
   ```html
   <aside id="sources-drawer" class="hidden fixed top-0 right-0 h-full w-96 bg-white border-l border-gray-200 shadow-xl z-50 flex-col dark:bg-gray-800 dark:border-gray-700 overflow-y-auto">
       <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 sticky top-0 bg-white dark:bg-gray-800">
           <h2 class="text-sm font-semibold text-gray-800 dark:text-gray-100">Nguồn tham khảo</h2>
           <button type="button" id="sources-drawer-close" class="text-gray-400 hover:text-gray-600"><i class="fa-solid fa-xmark"></i></button>
       </div>
       <div id="sources-drawer-body" class="flex-1 px-4 py-3"></div>
   </aside>
   <div id="sources-drawer-backdrop" class="hidden fixed inset-0 bg-black/20 z-40"></div>
   ```

8. **`Pages/Chat/Index.cshtml`** — in the assistant-message `else` block (currently lines 142-189),
   replace the `cites.Count > 0` flat-chip block with a call to `IndexModel.BuildSourcePanel(msg.SourcesJson, msg.SourceDocumentIdsJson)`,
   and render (when `Entries.Count > 0`):
   - a `.sources-trigger` button with text `Nguồn tham khảo (@entries.Select(e => e.DocumentId).Distinct().Count())`
   - immediately followed by a sibling
     `<script type="application/json" data-role="sources-payload">@Html.Raw(JsonSerializer.Serialize(entries, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase }))</script>`
     — see Architecture Decision 3 for why this specific serialization call is safe.
   Wrap the outer message `<div class="flex gap-3">` with `data-message-wrapper="@msg.Id"` so
   `initHistoryCitations` (step 12) can scope its DOM queries per message.

9. **`wwwroot/js/chat.js`** — in `appendAssistantBubble()`, replace the
   `<div data-sources class="hidden flex-wrap gap-1.5 mb-3"></div>` line with a `.sources-trigger`
   button (same classes/markup as step 8's Razor version, `hidden` by default, becomes
   `inline-flex` when populated). Return `sourcesTriggerEl: wrap.querySelector(".sources-trigger")`
   from the function alongside the existing returned fields.

10. **`wwwroot/js/chat.js`** — new function `openSourcesDrawer(sources)`: clears
    `#sources-drawer-body` (`replaceChildren()` — never `innerHTML` with dynamic content), groups
    `sources` by `documentId` preserving first-seen order, renders one non-collapsible `<h3>`
    sub-header per document (title via `textContent`, plus a download `<a href="/api/documents/{documentId}/download" target="_blank" rel="noopener">Tải tài liệu gốc</a>`),
    then one card per chunk with `card.dataset.sourceIdx = String(item.idx)`, snippet via
    `textContent`, and `Điểm liên quan: {score.toFixed(3)}` via `textContent` (never "%", never
    "match"). Call `showDrawer()` at the end.

11. **`wwwroot/js/chat.js`** — new `showDrawer()`/`hideDrawer()` functions (toggle `.hidden` on
    `#sources-drawer` + `#sources-drawer-backdrop`); wire `#sources-drawer-close` click and backdrop
    click to `hideDrawer()` once at IIFE init time (same section as the existing `[data-thumb]`
    init block at the bottom of the file).

12. **`wwwroot/js/chat.js`** — new `initHistoryCitations()` (called once at IIFE init, alongside the
    existing feedback-button wiring): for each `[data-message-wrapper]`, find its `.sources-trigger`
    and adjacent `script[data-role="sources-payload"]`; `JSON.parse` the script's `textContent`
    (already camelCase per step 8); if non-empty, wire the trigger's click to
    `openSourcesDrawer(sources)` and call `wireCitations(wrapperEl, sources)` (step 15).

### Layer 2 — Inline citation chips

13. **`Pages/Chat/Index.cshtml.cs`** — add:
    ```csharp
    private static readonly Regex CiteMarkerRegex = new(@"\[(\d+)\]", RegexOptions.Compiled);

    public abstract record AnswerSegment
    {
        public sealed record Text(string Content) : AnswerSegment;
        public sealed record Chip(int SourceIndex) : AnswerSegment;
    }

    public static IReadOnlyList<AnswerSegment> SplitAnswerIntoSegments(string content, int sourceCount)
    {
        var segments = new List<AnswerSegment>();
        int lastIndex = 0;
        foreach (Match m in CiteMarkerRegex.Matches(content))
        {
            if (m.Index > lastIndex) segments.Add(new AnswerSegment.Text(content[lastIndex..m.Index]));
            var n = int.Parse(m.Groups[1].Value);
            segments.Add(n is >= 1 && n <= sourceCount
                ? new AnswerSegment.Chip(n)
                : new AnswerSegment.Text(m.Value));
            lastIndex = m.Index + m.Length;
        }
        if (lastIndex < content.Length) segments.Add(new AnswerSegment.Text(content[lastIndex..]));
        return segments;
    }
    ```
    `sourceCount` argument is `hasRichData ? entries.Count : 0` at the call site (step 14) — this is
    what forces legacy rows to render zero chips (Architecture Decision 5 / AC12).

14. **`Pages/Chat/Index.cshtml`** — inside the assistant message `<p>` currently rendering
    `@msg.Content` directly, replace with a `@foreach (var seg in IndexModel.SplitAnswerIntoSegments(msg.Content, hasRichData ? entries.Count : 0))`
    loop: `AnswerSegment.Text` → `@t.Content` (Razor auto-HTML-encodes); `AnswerSegment.Chip` → a
    `<sup class="citation-chip cursor-pointer text-blue-600 hover:underline mx-0.5" data-source-idx="@c.SourceIndex">[@c.SourceIndex]</sup>`.

15. **`wwwroot/js/chat.js`** — new `renderCitationChips(assistant)`: read `assistant.answerEl.textContent`
    (the fully-assembled answer at `done` time), `replaceChildren()` on `answerEl`, then rebuild its
    children from `splitAnswerIntoSegments(text, assistant.normalizedSources?.length ?? 0)` — `Text`
    segments become `document.createTextNode(...)`, `Chip` segments become a `<sup>` element with
    class `citation-chip`, `dataset.sourceIdx = String(n)`, `textContent = '[' + n + ']'`. Call this
    from the `onDone` handler, AFTER `flushThinkAware(assistant)`.

16. **`wwwroot/js/chat.js`** — new `wireCitations(wrapperEl, sources)`: attaches
    `click → openSourcesDrawer(sources)` to the `.sources-trigger` (if present and `sources.length >
    0`), and to EVERY `.citation-chip` inside `wrapperEl`, attaches
    `click → { openSourcesDrawer(sources); highlightSource(idx); }` where `idx` comes from the
    chip's own `data-source-idx`. Call this from the live path right after step 15's
    `renderCitationChips`, using `assistant.normalizedSources`.

17. **`wwwroot/js/chat.js`** — new `highlightSource(idx)`: `document.querySelector('#sources-drawer-body [data-source-idx="' + idx + '"]')`, `scrollIntoView({block:"center",behavior:"smooth"})`, add a `ring-2 ring-blue-400` class for ~1.5s then remove it. If no matching card exists (defensive — should not happen given index parity), no-op silently.

18. **`wwwroot/js/chat.js`** — in the `onSources` handler (currently `renderSources(assistant.sourcesEl, docs)`),
    replace with: build `assistant.normalizedSources = docs.map((d,i) => ({ idx: i+1, documentId: d.document_id, title: d.title, snippet: d.snippet, score: d.score }))`,
    compute distinct-document count, populate and un-hide `assistant.sourcesTriggerEl`, and wire its
    click to `openSourcesDrawer(assistant.normalizedSources)`. If `docs.length === 0`, do nothing
    (trigger stays hidden — AC3).

### Layer 3 — Full-chunk view proxy endpoint (STRETCH — recommended fast-follow, NOT this EXECUTE pass)

**Scheduling recommendation:** defer to a fast-follow plan. Rationale: zero existing precedent for
Qdrant fetch-by-id + tenant filter in this codebase (`vectorstore.py` has no `retrieve()`/`scroll()`
method today — confirmed by reading the file in full), it is explicitly SPEC'd as stretch/deferred
scope, and Layers 1/2/4 alone fully satisfy the SPEC's primary user stories (officials can already
see full titles/snippets/scores and download the original document). Shipping Layer 3 in the same
EXECUTE pass would add an entirely new Python + .NET surface with a security-critical tenant-check
requirement to a plan that is already touching 8 files across 2 render paths.

If/when scheduled, the checklist is (fully designed now so no future ambiguity):

19. **`worker/app/services/vectorstore.py`** — add `get_chunk_by_id(chunk_id: str, department_id: str) -> dict | None`:
    call `self._client.retrieve(collection_name=self._collection, ids=[chunk_id], with_payload=True)`,
    then **mandatory post-fetch check**: `if not points or points[0].payload.get("department_id") !=
    department_id: return None` (never return the point on a tenant mismatch, and never distinguish
    "wrong tenant" from "doesn't exist" in the response — both return `None` → 404). This is the
    CHOSEN approach over building a combined `Filter(must=[HasIdCondition(has_id=[chunk_id]),
    FieldCondition(department_id)])` via `scroll()` — functionally equivalent, but a single-id
    `retrieve()` + post-fetch check is simpler for a single-point lookup.

20. **STRIDE quick-scan (Layer 3, tenant boundary):**

    | Threat | Applies? | Mitigation |
    |---|---|---|
    | Spoofing | No — reuses existing auth cookie + `DepartmentId` claim | n/a |
    | Tampering | Yes — caller could pass a valid chunk id from another department | Step 19's mandatory post-fetch check |
    | Repudiation | Low | Optional: audit log entry mirroring `doc.download` pattern (nice-to-have) |
    | **Information Disclosure** | **PRIMARY THREAT** — cross-department chunk text leak if the check is skipped | Step 19's check is non-optional; this is the VALIDATE gate item |
    | Denial of Service | Low — single cheap point lookup | n/a |
    | Elevation of Privilege | No — same `X-Worker-Api-Key` auth as every worker endpoint | n/a |

21. **`worker/app/api/documents.py`** — new `GET /chunk/{chunk_id}` route (under the existing
    `/documents` prefix + `require_api_key` dependency), query param `department_id` (required),
    returns `{id, document_id, title, text}` (full untruncated text) or 404.

22. **`Infrastructure/AiWorker/IAiWorkerClient.cs` + `AiWorkerClient.cs`** — new
    `Task<ChunkDetail?> GetChunkAsync(string chunkId, CancellationToken)`, mirroring
    `DeleteDocumentAsync`'s HTTP-call/exception-handling shape (lines 131-155).

23. **New/extended .NET controller action** — tenant-scoped exactly like `DocumentsController`
    (`TryGetDepartmentId` pattern, line 352), proxies to `IAiWorkerClient.GetChunkAsync`, passing the
    caller's own `DepartmentId` claim (never a client-supplied value) as the `department_id` query
    param.

24. **`worker/app/services/test_chunk_fetch_tenant.py`** (new pytest file) — following
    `test_retriever_fusion.py`'s mock-Qdrant-client pattern: (a) same-tenant fetch returns the
    chunk, (b) cross-tenant fetch (mock point with a different `department_id` payload) returns
    `None`, (c) non-existent id returns `None`. This is a REQUIRED Fully-Automated gate before
    Layer 3 can be marked done — matches the SPEC's high-risk-class rule (auth/tenant boundary
    minimum tier = Hybrid, and this is cheaper to make Fully-Automated given `pytest` already
    exists in `worker/`).

---

## Test Coverage Plan (`vc-test-coverage-plan` discipline)

Context confirmed before tiering: repo has ZERO C# test project (`process/context/tests/all-tests.md`
"Known Gaps") and no JS test harness; `worker/` has `pytest` with a working precedent
(`test_retriever_fusion.py`). This plan does NOT require scaffolding new C#/JS test infra — see
Test Infra Improvement Notes for what's recommended as a future backlog item instead.

### High-Risk Classes

| Area | High-risk class | Minimum tier | Gap rationale if known-gap accepted |
|---|---|---|---|
| Layer 4 schema migration | schema/data migration | Hybrid | Not accepted as known-gap — Fully-Automated (migration apply) + Hybrid (DB row inspection) both run |
| Layer 3 tenant check (deferred) | auth/tenant boundary | Hybrid | Not applicable this pass — Layer 3 code does not ship; when scheduled, step 24's pytest is a REQUIRED Fully-Automated gate, no known-gap accepted then either |

### Area: Layer 4 — EF Core migration + ChatMessage schema

| Tier | Scenario | Command / Steps | What it proves | What it does NOT prove |
|---|---|---|---|---|
| Fully-Automated | Migration compiles and applies | `dotnet build` then `dotnet ef database update` | Migration is syntactically valid and applies cleanly to a real DB | Data correctness of what gets written |
| Fully-Automated | Existing column untouched (AC13) | Code review of migration diff — confirm exactly one `AddColumn`, zero touches to `SourceDocumentIdsJson` | Column width/shape didn't change | Runtime write behavior |
| Hybrid | Full source list persisted (AC14) | Send a live query (needs full stack running: .NET + worker + Qdrant + SQL) then `SELECT SourcesJson FROM ChatMessages WHERE Id = '<new-id>'` | The new column captures the full per-chunk shape, not just ids | Long-term migration correctness under concurrent load |
| Agent-Probe | Legacy row reload (AC12) | Manual: pick a `ChatMessages` row created before this migration, reload its conversation | Legacy fallback renders without crash/blank panel | Exhaustive legacy-shape coverage (only the two known legacy shapes are tested) |

Gap resolution:

| Gap | Resolution options |
|---|---|
| No automated test asserts `BuildSourcePanel`/`SplitAnswerIntoSegments` output shape | A) Add a new xUnit project + 6-8 unit tests for these 2 pure functions — ~1-2 hrs, cheapest possible first C# test (no DB/HTTP dependency). B) N/A — no infra to stand up. C) Accept as known-gap this pass — rationale: repo-wide constraint explicitly excludes scaffolding new test infra from this feature's scope (SPEC Constraints); these are pure functions, low regression risk, and are called out in Test Infra Improvement Notes as the prime first candidate. D) Backlog note: `add-csharp-unit-tests-for-pure-citation-functions_NOTE_16-07-26.md` in `process/general-plans/backlog/`. |

### Area: Layer 1/2 — Drawer + chips (chat.js + Razor)

| Tier | Scenario | Command / Steps | What it proves | What it does NOT prove |
|---|---|---|---|---|
| Agent-Probe | AC1 panel appears, correct N | Manual browser: send question with known sources, confirm trigger button + count | Panel visibility + document-count math | Automated regression over time |
| Agent-Probe | AC2 grouping + no "%" | Manual browser: open drawer, visually confirm document sub-headers + no percentage text | Grouping + score-label correctness | — |
| Agent-Probe | AC3 zero-sources | Manual browser: greeting-only prompt, confirm no button/error | Empty-state handling | — |
| Hybrid | AC4 download link | Manual click + code review confirming the href targets the existing `/api/documents/{id}/download` route (no new endpoint invented) | Reuse of existing tenant-scoped download flow | Download flow's own correctness (already covered by that endpoint's existing behavior) |
| Agent-Probe | AC5 chip click→scroll+highlight | Manual browser with cloud provider (Gemini) | Chip interaction wiring | — |
| Agent-Probe | AC6 malformed/out-of-range | Manual: force `[9]` w/ 4 sources | Safe degradation | Every conceivable malformed input (regex is `\[(\d+)\]`, digits only, by design) |
| Agent-Probe | AC7 zero markers | Manual browser with local Ollama | Layer 1 stands alone | — |
| Agent-Probe | AC8 duplicate markers | Manual: crafted duplicate `[1]` | Consistent mapping | — |
| Hybrid | AC9 no HTML injection | Grep check: `grep -n "innerHTML" wwwroot/js/chat.js` — confirm every match is either an empty-string clear (existing precedent, `renderSources`'s old clear line, or the new `replaceChildren()` calls which don't use innerHTML at all) or a STATIC template string with no interpolated dynamic value, PLUS a manual probe sending a title/snippet containing `<script>` or `'` | No dynamic value flows through `innerHTML`; static-template-then-`textContent`-fill pattern holds | Every future code path (this is a point-in-time check) |

Failing stub (Fully-Automated rows only — none in this area qualify as Fully-Automated; all
UI-facing rows are Agent-Probe/Hybrid per the repo's accepted test-infra gap. No TDD stub applies.)

### Missing Test Areas

| Area | Why untestable in this plan | Resolution chosen |
|---|---|---|
| Concurrent reload while a live stream is still writing `SourcesJson` | Requires 2+ concurrent browser sessions against the same conversation; out of this plan's manual-probe scope | Backlog: note as a known theoretical race in Test Infra Improvement Notes; existing `finally`-block persistence pattern in `ChatService` already handles the single-writer case correctly |
| Layer 3 tenant-check (until scheduled) | Code does not exist yet this pass | Deferred — step 24 fully specifies the required pytest for when it IS scheduled; not a known-gap on shipped code because no Layer 3 code ships |

---

## Verification Evidence

| Gate / Scenario | Strategy | Proves SPEC criterion |
|---|---|---|
| Manual browser: send question with known sources, confirm trigger button + doc count | Agent-Probe | AC1 |
| Manual browser: open drawer, confirm grouping + snippet + no "%" | Agent-Probe | AC2 |
| Manual browser: greeting-only prompt, confirm no panel/error | Agent-Probe | AC3 |
| Manual click + code review of download href | Hybrid | AC4 |
| Manual browser (Gemini): chip click → scroll+highlight | Agent-Probe | AC5 |
| Manual browser: force `[9]` w/ 4 sources + split-chunk marker (structurally moot — done-time re-scan) | Agent-Probe | AC6 |
| Manual browser (local Ollama, zero markers) | Agent-Probe | AC7 |
| Manual browser: duplicate `[1]` markers | Agent-Probe | AC8 |
| Grep + manual crafted-payload probe | Hybrid | AC9 |
| Manual browser: send message, reload, compare panel content | Agent-Probe | AC10 |
| Manual browser: compare live title vs. post-reload title, same message | Agent-Probe | AC11 |
| Manual browser: reload a pre-migration conversation | Agent-Probe | AC12 |
| Code review of migration diff (single AddColumn, `SourceDocumentIdsJson` untouched) | Fully-Automated | AC13 |
| `dotnet ef database update` applies cleanly | Fully-Automated | AC13 |
| Manual browser + direct `SourcesJson` DB row inspection | Hybrid | AC14 |
| (deferred) pytest tenant-check per step 24 | Fully-Automated (when scheduled) | AC15 |

---

## Risk Predictions

**1. Schema migration + legacy fallback correctness (highest risk item).**
Prediction: the single biggest way this breaks in practice is a legacy `SourceDocumentIdsJson`
row whose shape doesn't match either of the two tolerated legacy shapes (bare-string-array or
`{id,title}`-object-array) — e.g. a `null` root, a non-array root, or a row where the JSON is
simply malformed from some earlier bug. Mitigation already designed in: `BuildSourcePanel`'s outer
`try/catch` returns `(Array.Empty<SourcePanelEntry>(), false)` on ANY exception, matching the
existing `ParseSourceCites`'s defensive pattern exactly — this was a pre-existing safety net, this
plan does not weaken it. Edge case worth an explicit manual probe during EXECUTE: a row with
`SourceDocumentIdsJson = "null"` (the literal 4-char string) — `JsonDocument.Parse("null")` succeeds
and `RootElement.ValueKind` is `Null`, not `Array`, so the existing `if (doc.RootElement.ValueKind
!= JsonValueKind.Array) return ...;` guard already handles it correctly. No code change needed;
noting this so EXECUTE doesn't second-guess the guard.

**2. `Html.Raw` JSON-into-`<script>` embedding (security-relevant judgment call).**
Prediction: the biggest risk here isn't the mechanism (System.Text.Json's default encoder is a
real, documented .NET safety guarantee) — it's a FUTURE maintainer accidentally reusing this
`Html.Raw` call site with a custom `JsonSerializerOptions` that has a relaxed encoder (e.g. copy-pasting
this snippet elsewhere and adding `Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping` to "fix"
some Vietnamese-diacritics-look-ugly-as-\\uXXXX complaint, not realizing that also disables the
HTML-safety escaping). Mitigation: Architecture Decision 3's explicit warning comment MUST also be
placed as a code comment directly above the `Html.Raw(JsonSerializer.Serialize(...))` call site in
`Index.cshtml` during EXECUTE (checklist item 8) — not just in this plan document.

**3. Regex chip parsing on large/unusual answers.**
Prediction: low risk. `\[(\d+)\]` on a long Vietnamese administrative answer with many numbered
list items (e.g. "1. ... 2. ... [1] ...") could theoretically produce a lot of segments, but this
is bounded by answer length and is a client-side/server-side string operation with no loop-unsafe
construct (standard regex `Matches`/`MatchCollection` iteration, no backtracking risk since the
pattern has no nested quantifiers). No mitigation needed beyond what's already designed.

---

## Test Infra Improvement Notes

- **Prime candidate for the repo's first C# unit tests:** `SourcePanelEntry`/`BuildSourcePanel` and
  `AnswerSegment`/`SplitAnswerIntoSegments` (`Pages/Chat/Index.cshtml.cs`) are pure functions with
  zero DB/HTTP dependency — the cheapest possible starting point for standing up an xUnit project
  when the repo is ready to invest in one (~6-8 test cases: empty input, rich-data happy path,
  legacy bare-id-array shape, legacy object-array shape, malformed JSON, out-of-range chip index,
  duplicate chip index, zero markers).
- **JS test harness:** none exists; `splitAnswerIntoSegments` in `chat.js` is the JS-side twin of the
  same pure-function opportunity above, if/when a JS test runner is introduced.
- **Concurrent-write race** (live stream still persisting `SourcesJson` while another tab reloads the
  same conversation) is untested and theoretical; not blocking, flagged for awareness only.

---

## Resume and Execution Handoff

1. **Selected plan file path:** `process/general-plans/active/rag-source-citations_16-07-26/rag-source-citations_PLAN_16-07-26.md` (this file)
2. **Last completed phase/step:** PLAN just completed. VALIDATE has not yet run.
3. **Validate-contract status:** pending — placeholder below, to be written by `vc-validate-agent`
4. **Supporting context files loaded during PLAN:** SPEC file (full, `rag-source-citations_SPEC_16-07-26.md`); `process/context/all-context.md`; `process/context/tests/all-tests.md`; `process/context/planning/all-planning.md`; `.claude/skills/vc-generate-plan/references/generate-plan.md`; source files read in full or in relevant part: `Models/ChatMessage.cs`, `Services/Chat/ChatService.cs`, `Services/Chat/IChatService.cs`, `Pages/Chat/Index.cshtml.cs`, `Pages/Chat/Index.cshtml`, `wwwroot/js/chat.js`, `Infrastructure/AiWorker/Contracts/QueryEvent.cs`, `Migrations/20260616072428_AddUserAvatarPath.cs`, `data/ApplicationDbContext.cs` (ConfigureChatMessage section), `Controllers/Api/DocumentsController.cs`, `Infrastructure/AiWorker/IAiWorkerClient.cs`, `Infrastructure/AiWorker/AiWorkerClient.cs` (DeleteDocumentAsync), `worker/app/api/documents.py`, `worker/app/api/query.py`, `worker/app/services/retriever.py`, `worker/app/services/vectorstore.py`, `worker/app/services/prompt_builder.py`, `worker/app/services/prompts/rag_system.j2`, `worker/app/services/test_retriever_fusion.py`.
5. **Next step for a fresh agent picking up mid-execution:** run `ENTER VALIDATE MODE` against this
   plan file. VALIDATE should pay particular attention to: (a) the migration/legacy-fallback
   correctness claims in Risk Prediction 1, (b) the `Html.Raw` JSON-embedding safety claim in
   Architecture Decision 3 / Risk Prediction 2 (security dimension), (c) whether the Layer 3
   fast-follow deferral recommendation is acceptable to the user before EXECUTE begins.

**Environment note:** the Bash tool was unavailable for this entire PLAN session (every invocation,
including trivial ones like `echo hello`, failed with a shell-wrapper parse error unrelated to
command content). The plan-artifact validator
(`node .claude/skills/vc-generate-plan/scripts/validate-plan-artifact.mjs <this-file>`) and the
`date +%d-%m-%y` command could NOT be run this session — the date (16-07-26) was instead confirmed
from the existing SPEC file's frontmatter and task-folder name, and matches the session's known
current date. **The orchestrator or a fresh agent with working Bash should run the validator against
this file before EXECUTE begins.**

---

## Validate Contract

(placeholder — vc-validate-agent writes this section before EXECUTE)

---

## Cursor + RIPER-5 Guidance

- **RIPER-5:** This plan is complete. Say `ENTER VALIDATE MODE` to proceed to plan validation
  (required before implementation) — do not skip to EXECUTE.
- **Cursor Plan mode:** Import the Implementation Checklist (24 items across 4 layer sections)
  directly. Execute Layer 4 → Layer 1 → Layer 2 in order (each depends on the prior layer's data
  shape or DOM being in place); Layer 3 is deferred per the explicit scheduling recommendation
  above — do not start it without a fresh confirm from the user.
- After each layer: stop and run that layer's Test Procedure from the Execution Brief before
  proceeding to the next.
