---
name: plan:rag-source-citations-spec
description: "Product-discovery SPEC for Gemini-style source citations UI (source panel + inline chips + persistence)"
date: 16-07-26
feature: general-plans
---

# SPEC — Gemini-Style Source Citations UI

## Summary

Right now, when the chatbot answers a question, officials have no easy way to see *which*
government document(s) the answer came from, or to check the exact passage the AI used before
trusting a number or a legal requirement. This feature adds a "sources" panel under every AI
answer (like Gemini/Perplexity-style citations) showing which documents and passages fed the
answer, plus optional clickable footnote markers (`[1]`, `[2]`) inside the answer text that jump
to the matching passage. Officials can verify claims in seconds instead of re-opening source PDFs
and searching manually. This also fixes a pre-existing bug where reloading an old conversation
shows a generic "Tài liệu" (Document) label instead of the real document title.

## User Stories / Jobs To Be Done

1. **As a government official reading an AI answer**, I want to see which documents and passages
   the answer is based on, so that I can trust — or catch a mistake in — the AI's response before
   acting on it (e.g. before citing a budget figure in a report).

2. **As a government official**, I want to click a citation marker like `[1]` inside the answer
   text and immediately see the exact passage it refers to, so that I don't have to read the whole
   source panel to find the right one.

3. **As a government official reviewing an old conversation**, I want the same source information
   to still be there after I reload the page or come back later, so that I can verify an answer
   days after the original conversation.

4. **As a government official**, I want to open the original source document from the sources
   panel, so that I can read more context than the short passage shown.

5. **As an admin auditing chatbot usage**, I want confidence that citation numbers always point to
   the correct document — never a mismatched or missing one — so that officials are not misled by
   a broken citation.

## What The User Wants (Behavioral Outcomes)

- Every AI answer that used retrieved documents shows an expandable **sources panel** underneath
  it, listing each source document with a short passage (snippet) that fed the answer, grouped by
  document.
- When the AI's answer includes bracketed numbers like `[1]`, `[2]`, those numbers appear as small
  clickable superscript markers in the answer text itself. Clicking or hovering one highlights /
  scrolls to the matching entry in the sources panel and shows a quick preview.
- The sources panel appears whether the AI happens to write citation markers or not — the panel
  never depends on `[1]`/`[2]` being present. (The local model does not reliably produce them; the
  panel must still be useful on its own.)
- Nothing breaks if the AI writes a marker number that doesn't exist (e.g. `[9]` when only 4
  sources were retrieved) — it's rendered as plain, non-clickable text.
- Reloading the page, or coming back to an old conversation, shows the same sources panel with the
  same real document titles — including for messages sent before this feature existed (those fall
  back gracefully, not to a broken state).
- The panel score is shown as a relative ranking indicator (e.g. "best match" ordering / a bar or
  label), never presented as a percentage or "% match", because the underlying number is not a
  similarity percentage.
- All panel text is in Vietnamese, matches the existing chat visual style (light + dark mode), and
  never renders raw HTML from the AI's answer or from a document title (no injection risk).
- From the sources panel, the official can open/download the original source document (existing
  tenant-scoped download link), if the entry is a real, previously uploaded document.

## Flow / State Diagram

```
[User sends question]
        |
        v
[AI worker retrieves N chunks] --SSE event: sources--> [Browser receives {id, document_id,
        |                                                title, snippet, score}[] BEFORE tokens]
        v
[AI streams answer tokens] --SSE event: token (repeated)--> [Answer text renders incrementally]
        |
        v
[SSE event: done]
        |
        v
   +-------------------------------------------+
   |  Assistant message bubble now shows:       |
   |                                             |
   |  <answer text with optional [1][2] chips>  |
   |                                             |
   |  [ Nguồn tham khảo (3) ▾ ]  <- collapsed    |
   |     (click to expand)                       |
   +-------------------------------------------+
              |
              | user clicks to expand
              v
   +-------------------------------------------+
   | Nguồn tham khảo (3)                        |
   | -------------------------------------------|
   | 📄 Document A                              |
   |    "...500-char snippet..."     [score]    |
   |    "...another snippet from A..." [score]  |
   |    [Tải tài liệu gốc]                       |
   | -------------------------------------------|
   | 📄 Document B                              |
   |    "...snippet..."              [score]    |
   +-------------------------------------------+

Inline chip interaction:
  "...ngân sách là 500 triệu đồng[1]..."
                              |
                        click/hover [1]
                              v
                panel entry for source #1 highlighted
                + scrolled into view + tooltip snippet shown

Reload / history path:
  [Page reload] --> [Razor server render reads persisted sources JSON per message]
        |
        v
  [Same sources panel renders from saved data] (chips optional — see Open Questions)
        |
        v (legacy message, pre-feature, only has bare doc-id JSON)
  [Panel falls back to titles it can resolve via existing document lookup;
   never shows blank/broken panel, never crashes the page]
```

## Acceptance Criteria (Testable Outcomes)

### Layer 1 — Source panel

1. **AC1 — Panel appears for any answer that used retrieved sources.**
   Given a live chat answer that used ≥1 retrieved chunk, when the answer finishes streaming, then
   a collapsed "Nguồn tham khảo (N)" panel appears under the assistant bubble, where N = number of
   distinct documents.
   `proven by:` manual browser scenario — send a question with known source docs, verify panel
   appears with correct document count. (No automated E2E harness exists for this UI yet — see
   Constraints.)
   `strategy:` Agent-Probe.

2. **AC2 — Panel groups chunks by document, shows title + snippet + relative score per chunk.**
   Given the SSE `sources` payload contains N chunks across M documents, when the panel expands,
   then it shows M document groups, each listing its own chunk snippets (≤500 chars) with a
   relative-ranking indicator — never a raw percentage.
   `proven by:` manual browser scenario verifying grouping + no "%" text appears near score.
   `strategy:` Agent-Probe.

3. **AC3 — No sources retrieved → no panel, no error.**
   Given the AI answers without using retrieval (empty `sources` event, e.g. a greeting), when the
   answer finishes, then no sources panel is rendered and no console error occurs.
   `proven by:` manual browser scenario with a non-retrieval prompt.
   `strategy:` Agent-Probe.

4. **AC4 — Download link works from panel.**
   Given a document group in the panel, when the official clicks "Tải tài liệu gốc", then the
   existing tenant-scoped `/api/documents/{id}/download` flow is invoked (same permission rules as
   the admin document list).
   `proven by:` manual browser scenario + code inspection of the reused download endpoint call.
   `strategy:` Hybrid.

### Layer 2 — Inline citation chips

5. **AC5 — Markers render as clickable chips when present.**
   Given the answer text contains `[1]`, `[2]`, etc., when the answer is fully rendered, then each
   valid marker (index within the retrieved source count) becomes a small superscript chip that,
   on click, scrolls to and highlights the matching panel entry, and on hover shows the snippet in
   a tooltip.
   `proven by:` manual browser scenario using a cloud provider (Gemini) known to emit markers
   reliably.
   `strategy:` Agent-Probe.

6. **AC6 — Out-of-range or malformed markers degrade safely.**
   Given the answer contains `[9]` but only 4 sources were retrieved, or a marker split across two
   SSE token chunks (e.g. `"[1"` then `"]"`), when the answer renders, then the marker is either
   correctly reassembled and treated as a normal chip, or (if truly out of range) rendered as
   plain inert text — never a broken/crashed render, never a clickable dead link.
   `proven by:` manual browser scenario forcing a split-chunk marker and an out-of-range marker.
   `strategy:` Agent-Probe.

7. **AC7 — Zero markers is a fully supported, non-degraded state.**
   Given the local model (qwen2.5:3b) emits an answer with no `[N]` markers at all, when the
   answer renders, then the sources panel (Layer 1) still appears complete and useful — Layer 2
   simply contributes nothing extra. This is not treated as an error state.
   `proven by:` manual browser scenario with local Ollama provider.
   `strategy:` Agent-Probe.

8. **AC8 — Duplicate markers map to the same source consistently.**
   Given the answer repeats `[1]` twice, when either occurrence is clicked, then both point to the
   same panel entry (source #1 in SSE array order).
   `proven by:` manual browser scenario with a crafted duplicate-marker answer.
   `strategy:` Agent-Probe.

9. **AC9 — No HTML injection via citation rendering.**
   Given an answer or a document title contains literal `<`, `>`, or script-like text, when
   rendered as part of citation chips or panel entries, then it is inserted via safe DOM APIs
   (`textContent`/`createTextNode`) and never interpreted as HTML.
   `proven by:` code review checklist item (no `innerHTML` with dynamic values) + manual probe
   with a crafted payload string.
   `strategy:` Hybrid.

### Layer 4 — Persistence (+ folded-in title-loss bug fix)

10. **AC10 — Sources survive page reload.**
    Given a conversation with an assistant answer that showed a sources panel, when the page is
    reloaded (or the conversation is reopened later), then the same sources panel (same
    documents, same snippets) reappears — sourced from persisted data, not re-computed.
    `proven by:` manual browser scenario: send message, reload, compare panel content before/after.
    `strategy:` Agent-Probe.

11. **AC11 — Real document titles show after reload (bug fix).**
    Given a message was answered using real source documents, when the conversation is reloaded,
    then the panel/chips show the actual document titles — never the generic "Tài liệu" fallback
    label, for any message created after this feature ships.
    `proven by:` manual browser scenario comparing live-stream title vs. post-reload title for the
    same message.
    `strategy:` Agent-Probe.

12. **AC12 — Legacy messages (pre-feature) degrade gracefully.**
    Given a ChatMessage row created before this feature shipped (only has the old bare
    `SourceDocumentIdsJson` id-list, no per-chunk snippet/score data), when that conversation is
    reloaded, then the UI shows whatever it can (document titles resolved via existing lookup, or
    a neutral fallback) without throwing an error, without a blank/broken panel, and without
    inline chips (since no snippet data exists for them).
    `proven by:` manual browser scenario using a conversation from before the schema change.
    `strategy:` Agent-Probe.

13. **AC13 — New column does not affect the existing legacy field.**
    Given the schema change adds a new nullable column for per-chunk source data, when a message
    is saved, then the existing `SourceDocumentIdsJson` (nvarchar(4000)) column is untouched in
    width and continues to be written as before (no widening, no removal).
    `proven by:` code review of the EF Core migration diff (single `AddColumn` Up/Down).
    `strategy:` Fully-Automated (build/migration apply check) — running `dotnet ef database
    update` and confirming the migration applies cleanly is scriptable even without a full test
    suite.

14. **AC14 — Full per-chunk source list is captured, not just document ids.**
    Given a query returns source chunks with id/document_id/title/snippet/score, when
    `ChatService` persists the assistant message, then all of that per-chunk data (not just the
    bare document id) is saved to the new column.
    `proven by:` manual browser scenario + direct DB row inspection after a query.
    `strategy:` Hybrid.

### Layer 3 — Full-chunk view (stretch / follow-up scope)

15. **AC15 — (Stretch) Full chunk text is viewable on demand.**
    Given a panel entry shows only a 500-char snippet, when the official wants full context, a
    follow-up capability MAY let them fetch the complete chunk text for that specific source
    (tenant-filtered), without needing a new document upload.
    `proven by:` deferred — no test until this stretch layer is scheduled; tracked as a known-gap
    until INNOVATE/PLAN decide whether it ships in this pass or a follow-up.
    `strategy:` Agent-Probe (when built).

## Out Of Scope

- **Layer 5 — page-level PDF highlighting / chunk-to-page mapping.** Requires an ingest schema
  change (storing page/bbox provenance) and re-ingesting existing documents. Parked with the
  future Phase-2 provenance work.
- Building a new automated E2E/UI test framework (Playwright, Cypress, etc.) as part of this
  feature. The repo currently has zero C# tests and no JS test harness; this SPEC does not require
  scaffolding one. Verification for UI-facing criteria is manual/browser-driven for this pass (see
  Constraints).
- Any change to the underlying retrieval/ranking algorithm, the RRF fusion, or the reranker.
- Changing what `score` numerically means or how it's computed — this SPEC only forbids
  mislabeling it as a percentage in the UI.
- Multi-tenant cross-department source leakage protections — already enforced by the existing
  retriever; not re-verified here beyond the existing download-endpoint tenant check (AC4).
- Editing or correcting AI-generated citation markers (e.g. auto-inserting missing `[N]` markers
  when the model doesn't emit them) — Layer 2 is display-only, best-effort.

## Constraints

- **No wire format change without cause**: the SSE `sources` event already carries `id,
  document_id, title, snippet, score` — the UI features must be buildable from this existing
  payload; do not require re-fetching data over a new endpoint for Layer 1/2.
- **`score` must never be shown as a percentage/"% match"** — it is an RRF fusion rank score
  (or reranker cross-encoder score when active), not a cosine similarity.
- **Existing `SourceDocumentIdsJson` column (nvarchar(4000)) must not be widened** — the new
  persisted data goes in a new nullable column.
- **XSS discipline**: all dynamic text (titles, snippets, answer text) must use
  `textContent`/`createTextNode`, consistent with existing chat.js conventions — never
  `innerHTML` with dynamic values.
- **Must work on both rendering paths**: live SSE-streamed chat (chat.js) AND server-rendered
  history reload (Razor `Pages/Chat/Index.cshtml` + its parser) — a citation feature that only
  works live is incomplete per this SPEC (see Open Questions for whether Layer 2 specifically is
  v1-live-only or both).
- **Marker-to-source mapping is positional**: `[1]` = first source in SSE array order (matches
  the prompt template's `loop.index` numbering) — no other mapping scheme.
- **Vietnamese-only UI text**, Tailwind + `dark:` variants, FontAwesome 6.5.0 icon conventions,
  consistent with existing chip styling already used in chat.js.
- **No test-infra buildout required** — acceptance criteria are proven manually/by browser
  probe or code review where full automation isn't already available; this is a known,
  accepted gap for this feature pass, not a blocker.
- **EF Core migration must follow existing pattern**: `dotnet ef migrations add <PascalName>`,
  single `AddColumn` Up/Down, fluent config in `ApplicationDbContext.ConfigureChatMessage`.

## Open Questions

1. **Grouping UX**: should the sources panel show one entry per document with N snippets nested
   underneath (as drafted above), or a flat list of individual chunks (possibly with duplicate
   document titles)? — Owner: INNOVATE (approach comparison, no product ambiguity remains: user
   wants "grouped by document" per locked intent; INNOVATE decides visual nesting depth).
2. **Panel placement**: inline expandable panel directly under each message (as drafted), or a
   Gemini-style side "window"/drawer shared across the conversation? — Owner: INNOVATE. User's own
   word was "window" (Gemini-style), which could mean either; INNOVATE should compare both against
   existing single-column chat layout constraints.
3. **Does Layer 2 (inline chips) apply to reloaded/history messages in this same pass, or is it
   live-chat-only for v1, with history-chip rendering deferred?** — Owner: INNOVATE/PLAN. Affects
   whether the Razor history parser needs marker-parsing logic now or later. (Layer 1 panel is
   required on both paths regardless — this question is scoped to chips only.)
4. **Layer 3 scheduling**: ship the full-chunk-view endpoint as part of this same implementation
   pass, or explicitly as a fast-follow after Layers 1/2/4 ship? — Owner: PLAN, informed by
   INNOVATE's assessment of the Qdrant retrieve()+tenant-filter mechanism (no existing precedent).

None of the above block SPEC completion — they are approach-level decisions correctly deferred to
INNOVATE, not gaps in what the user wants. No `SPEC_INTENT_BLOCKED` condition applies.

## Background / Research Findings

Key facts from the completed RESEARCH phase that shaped these requirements:

- SSE `sources` event already delivers `{id, document_id, title, snippet, score}[]` before token
  streaming begins — no backend retrieval change needed for Layer 1/2, only rendering + capture.
- `score` is an RRF fusion rank (or post-reranker cross-encoder score), not a similarity
  percentage — driving the hard constraint against "% match" display.
- `chat.js`'s `feedThinkAware` already solves an incremental-parse-across-chunk-boundaries problem
  for `<think>` tags; Layer 2 marker parsing faces the identical problem (`"[1"` + `"]"` split
  across SSE token events) and should apply the same buffering discipline, or use a post-stream
  re-scan (`onDone`) — SPEC states the behavioral requirement (AC6) and leaves the implementation
  shape to INNOVATE/PLAN.
- Two independent rendering paths exist and currently diverge: live chat is JS
  (`wwwroot/js/chat.js`), history reload is server-rendered Razor (`Pages/Chat/Index.cshtml` +
  `ParseSourceCites`) — both must support Layer 1; Open Question 3 covers Layer 2 parity.
  `renderSources()` in chat.js currently only shows a flat deduped chip list (title only, no
  snippet/score, no grouping) — confirms Layer 1 grouping/snippet/score is new work, not already
  present.
- XSS convention confirmed by direct code read: `appendUserBubble` uses `textContent`, never
  `innerHTML` with dynamic values — this is the pattern Layer 1/2 must follow. Existing chip
  style: `text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded-full inline-flex items-center gap-1
  max-w-[14rem]` with `fa-file-lines` icon.
- Pre-existing bug (folded into Layer 4 scope by user approval): `ChatService` currently persists
  only bare document-id strings for `SourceDocumentIdsJson`; a `{id, title}` parsing path exists in
  the reload parser but is dead code because the writer never emits that shape — this is why
  reloaded conversations show generic "Tài liệu" instead of the real title. Same code path/column
  redesign as Layer 4's persistence work fixes both at once.
- Existing tenant-scoped download precedent: `GET /api/documents/{id}/download` (admin bypass +
  audit log) — reusable for the panel's "Tải tài liệu gốc" link (AC4), no new endpoint needed.
- EF migration precedent: `dotnet ef migrations add <PascalName>`, single `AddColumn` Up/Down,
  fluent config lives in `ApplicationDbContext.ConfigureChatMessage` — the new column must follow
  this exactly and must NOT touch the existing `SourceDocumentIdsJson` (nvarchar(4000)) field.
- Test reality: zero C# tests exist repo-wide; no JS test harness; only the Python worker has
  pytest. This SPEC does not require building new test infra — acceptance criteria for UI layers
  are proven via manual browser scenarios or code review, an explicitly accepted, scoped known-gap
  (see Constraints), not a vacuous-green shortcut — no criterion here claims automated coverage
  that doesn't exist.
- Layer 3 (full-chunk view) has no existing fetch-by-id precedent; Qdrant's `retrieve()` cannot
  combine an id lookup with a tenant filter in one call — would need a post-fetch
  `department_id` check or `scroll()` with a combined filter. This complexity is why Layer 3 is
  scoped as stretch/follow-up (Open Question 4), not committed to this pass.
- Explicit user decision: Layer 5 (page-level PDF highlighting) is out of scope — it needs an
  ingest schema change plus re-ingestion of existing documents, and is parked with future
  provenance work.
