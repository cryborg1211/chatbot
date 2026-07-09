---
name: plan:worker-precommit-fixes
description: "3-fix pre-commit worker batch: token-aware chunking (Fork 1), OCR partial-signal OR-merge (Fork 3), cross-layer partial-ingest status (Fork 2), plus noise-filter tightening (HIGH#1)"
date: 06-07-26
feature: general
phase: "n/a"
---

# Worker Pre-Commit Fixes — Implementation Plan

**Date**: 06-07-26
**Status**: Ready for VALIDATE
**Complexity**: COMPLEX (lightly — single session, cross-layer, no phase program)

Classification: **COMPLEX** (lightly) — single session, no phase program, but touches 2 runtimes
(Python worker + .NET) and a cross-layer contract (Fork 2), so it gets full touchpoint/blast-radius/
verification rigor instead of a bare checklist.

Source of truth for scope: `process/general-plans/backlog/code-debt_worker-audit_06-07-26.md`
(HIGH #1, #2; debt #16-#19 informed Fork 1 but only #16 is in scope here).

RESEARCH + INNOVATE are complete and LOCKED. This plan implements the locked decisions exactly —
no re-litigating chunking strategy, tokenizer choice, or the partial-ingest carrier shape.

---

## Overview

Three independent-but-related fixes bundled into one pre-commit batch, because they touch the same
files (`chunker.py`, `loader.py`) and the same commit boundary (worker diff currently uncommitted,
per `git status`):

1. **Fork 1 — token-aware chunking (#16, worker-only).** Chunk-size math is currently word-count
   based (`len(text.split())`) but the embedder's real cap (`embed_max_length=1024`) is in
   **tokens**. Vietnamese runs ~1.5-2.5 tokens/word; table cells worse. A 1024-word table chunk can
   hit 2000-3000+ tokens and get silently truncated by the embedder — retrieval blind to the tail of
   big tables. Fix: exact token counting at the two places it matters most (table splitting,
   final-emission safety net), word-based approximation everywhere else (cheap, coarse structural
   splits don't need per-call tokenizer overhead).

2. **Fork 3 — OCR double-pass OR-merge (#2 support, worker-only).** `_convert_pdf_batched` already
   detects and logs failed/dropped page batches but discards that signal. When a PDF fails digital
   extraction quality (`_is_near_empty`) and gets OCR-retried, BOTH passes may have independently
   dropped different page batches. Currently neither pass's partial signal survives to the caller.
   Fix: grow `_convert_pdf_batched`'s return to surface `(failed_batches, failed_ranges)`, and
   OR-merge both passes when OCR retry runs — any page dropped in *either* pass counts as partial.

3. **Fork 2 — partial-ingest signal + visible status (#2, CROSS-LAYER).** Once Fork 3 produces a
   partial signal, it has to travel worker → .NET → DB → UI so admins actually see "this doc is
   missing pages" instead of a silent, indistinguishable-from-complete "Ready" status. Additive,
   backward-compatible field threading across 6 files in 2 languages.

4. **HIGH #1 — noise-filter false positives (not a fork — no design choice, straight fix).**
   `_SIGNATURE_RE` in `chunker.py` matches any 2-6-word Titlecase phrase, which silently deletes
   ordinary Vietnamese headings and proper nouns ("Ủy Ban Nhân Dân"). Tighten the signal, add DEBUG
   logging of what gets filtered (chunker.py currently has no logger at all), and fix the sibling
   digit-strip bug (debt #9) in the same function while we're in there.

**Why bundled:** all four touch `worker/app/services/chunker.py` and/or `loader.py` inside the same
uncommitted diff; splitting into 4 separate plans would just create merge friction for zero benefit.
Each gets its own checklist section below so review/execution can still reason about them
independently.

---

## Locked Decisions (from INNOVATE — do not reopen)

These are implementation contracts, not open questions. The two items marked "OPEN for plan to
specify" are resolved below in **Plan Decisions**.

- **Tokenizer ownership:** `Chunker` owns its own tokenizer singleton via
  `AutoTokenizer.from_pretrained("BAAI/bge-m3")` (transformers' `AutoTokenizer` — a few-MB
  SentencePiece/tokenizer-config download, NOT the 2GB model weights the `Embedder` loads). Chunker
  must NOT reach into `Embedder`'s private `_model` internals — separate concern, separate object.
- **Injection site:** tokenizer/counter passed into `Chunker.__init__`. Exactly one construction
  site touches this: `worker/app/main.py:86-89` (`Chunker(chunk_size=..., chunk_overlap=...)`),
  which already runs after `Embedder` is built at `main.py:54`.
- **Hybrid precision:** EXACT bge-m3 token count only in `_split_table_block` (the function that
  decides row-boundary chunk splits) and at final chunk-emission (last-chance safety check before
  nodes are returned from `Chunker.split`). Word-ratio approximation stays for the coarse
  recursive-heading splits (`_split_markdown_by_headings`, `_recursive_split`, `_group_blocks`) —
  those only decide "does this section need to recurse further," not final chunk boundaries, so
  approximation is safe and keeps them cheap (no per-call tokenizer invocation on every heading
  section).
- **New pinned dependency:** `transformers` (currently transitive via `sentence-transformers` /
  `llama-index-embeddings-huggingface` — not declared explicitly in `worker/pyproject.toml`).
- **Fork 3 mechanism:** grow `_convert_pdf_batched`'s existing (but discarded) `failed_batches`
  count into a real return value carrying enough to build a partial-reason string; OR-merge digital
  pass + OCR-retry pass signals.
- **Fork 2 carrier:** `DoclingResult.partial: bool = False` + `DoclingResult.partial_reason: str |
  None = None`, threaded through `_load_docling` → `load_documents` → `ingest.py:_run_pipeline` →
  `IngestResponse` → .NET `IngestResult` → `DocumentService` → new `DocumentStatus.PartiallyIngested
  = 4` (appended, never renumbered) → UI status chip + summary counts.
- **HIGH#1 scope:** logging only added to `chunker.py` (it has none today); regex tightened to
  require a stronger/positional noise signal; digit-strip bug (debt #9) fixed in the same function
  if trivial (it is — one-line regex change).

---

## Plan Decisions (resolving the two OPEN items from the delegation brief)

### Decision A — token-overflow behavior at final-emission safety check

**Decision: re-split, never hard-truncate.**

Rationale: the whole point of this fix is anti-silent-loss (matches HIGH#1/HIGH#2 theme in the debt
file — silent deletion and silent partial ingestion are the two bugs already being fixed in this
same batch). A hard truncate at final emission would reintroduce exactly the silent-loss failure
mode this fork exists to close, just moved one layer down (embedder truncation → chunker
truncation). Concretely: if a chunk emitted by any earlier stage (recursive split, table split, or
merge step) still exceeds the token budget when exact-counted at final emission, re-run
`_split_table_block`'s row-boundary splitting logic on it (treating the whole chunk as one
"table-like" oversized block, since by this point in the pipeline the only realistic way a chunk
still overflows tokens after word-based splitting is dense numeric/table content or a merge that
pushed it over — see Decision C). If the chunk has no clean row/line boundary to split at (rare —
e.g. one giant unbroken prose paragraph), fall back to the existing `fallback_splitter`
(SentenceSplitter) applied to just that oversized chunk. This guarantees no single emitted chunk
ever exceeds the effective token cap silently — it either fits or gets further split, never
truncated.

### Decision B — reconciling `chunk_size` (words) vs `embed_max_length` (tokens)

**Decision: keep `chunk_size=1024` as the WORD-count structural-split target (approximate, coarse,
unchanged config default — no config.py behavior change for existing deployments), and introduce a
separate, explicit TOKEN cap equal to `settings.embed_max_length` (1024 tokens) enforced exactly at
the two hybrid-precision points (table split, final emission).**

Rationale: changing the existing `chunk_size` default's *meaning* (word→token) would silently
shrink every non-table chunk by ~2x for Vietnamese content with no plan-level signal — that's a
behavior change disguised as a bug fix, and it's not what INNOVATE locked (INNOVATE locked "hybrid
precision at two specific call sites," not "redefine chunk_size everywhere"). Instead:
- Word-based `chunk_size` (1024 words) continues to gate the coarse heading-recursion decision
  (`_split_markdown_by_headings`, `_recursive_split`, `_group_blocks`) — this determines *when to
  recurse further*, and being coarse/approximate here is fine because the hybrid-precision gates
  downstream will catch real overflows.
- A new **effective token target**, `_TABLE_TOKEN_CAP = settings.embed_max_length` (1024, read from
  the existing config value — no new config key), gates `_split_table_block`'s row-boundary
  decisions and the final-emission safety check, using EXACT tokenizer counts instead of
  `len(text.split())`.
- This means table chunks get precisely bounded at the real embedder limit (fixing the actual bug),
  while prose/heading chunks keep today's proven word-based sizing untouched. No `config.py` field
  changes are required — `chunk_size` and `embed_max_length` keep their current values and meanings;
  the fix is *which measurement function* is used at which call site, not new config semantics.

### Decision C — why re-split target uses `_split_table_block`'s logic even for non-table overflow

At final emission, a chunk that still overflows the token cap after all upstream splitting is,
empirically, almost always a table-like block (dense numbers/Vietnamese diacritics inflating token
count) or a merge artifact from `_merge_small_chunks` (Decision below). Reusing
`_split_table_block`'s row-boundary splitter (which requires `\n`-delimited lines, not literal
`|`-table syntax) is a pragmatic generic line-boundary splitter — it does not require the block to
be a literal markdown table, just line-delimited. Renaming is NOT required for this plan (avoid
scope creep); a plan comment at the call site is sufficient to document the reuse.

### Decision D — `_merge_small_chunks` interaction with the new token cap

`_merge_small_chunks` (chunker.py:371-395) can combine two under-`min_words` chunks into one that
*individually* passed the word-based cap but could exceed the token cap once merged (e.g. two small
table remnants). This is exactly why the final-emission safety check must run AFTER
`_merge_small_chunks` in `Chunker.split` (it already does — `_merge_small_chunks` is the last step
before `return all_nodes` at chunker.py:59-60) — the safety check is the backstop for this exact
case. No change to merge logic itself; the safety check net catches its output.

---

## Touchpoints

### Fork 1 — token-aware chunking (worker-only)

| File | Lines (current) | Change |
|---|---|---|
| `worker/app/services/chunker.py` | 1-26 (module docstring, imports) | Add `logging` import + module logger (also serves HIGH#1); add `transformers.AutoTokenizer` import (lazy, inside `__init__` like other heavy imports in this file) |
| `worker/app/services/chunker.py` | 28-34 (`Chunker.__init__`) | Add tokenizer singleton construction; store as `self._tokenizer` |
| `worker/app/services/chunker.py` | 264-328 (`_split_table_block`) | Replace `len(text.split())` word counts with exact tokenizer counts for `prefix_tokens`, `row_tokens`, `current_token_count` comparisons; function signature grows to accept the tokenizer (or a bound counter callable) |
| `worker/app/services/chunker.py` | 37-60 (`Chunker.split`) | Add final-emission safety check after `_merge_small_chunks`, before `return all_nodes`: exact-count every node's tokens, re-split any that exceed `_TABLE_TOKEN_CAP` via the table-split re-split path (Decision A/C) |
| `worker/app/services/chunker.py` | 207-258 (`_group_blocks`) | Pass tokenizer/counter through to `_split_table_block` call at line ~242 |
| `worker/app/main.py` | 86-89 (`Chunker(chunk_size=..., chunk_overlap=...)` construction) | Pass `settings.embed_max_length` (or the tokenizer itself) into the `Chunker` constructor call — the ONE construction site |
| `worker/pyproject.toml` | 7-48 (`dependencies` list) | Add explicit `"transformers>=4.40.0"` pin (compatible with `sentence-transformers>=3.0.0` already pinned) |

### Fork 3 — OCR double-pass OR-merge (worker-only)

| File | Lines (current) | Change |
|---|---|---|
| `worker/app/services/loader.py` | 256-318 (`_convert_pdf_batched`) | Change return type from `tuple[str, Any]` to `tuple[str, Any, int, list[tuple[int, int]]]` (merged_markdown, last_doc, failed_batch_count, failed_page_ranges) — capture `(start, end)` into a list whenever a batch is skipped (lines 297-308), instead of only incrementing a discarded local counter |
| `worker/app/services/loader.py` | 345-371 (`_load_docling`, PDF branch) | Capture the 4-tuple from both the digital-pass call (line 345-347) and the OCR-retry call (line 369-371); OR-merge: `partial = bool(digital_failed_ranges or ocr_failed_ranges)` when OCR retry ran, or just `bool(digital_failed_ranges)` when it didn't; build a human-readable `partial_reason` string listing merged failed ranges |
| `worker/app/services/loader.py` | 404-410 (`DoclingResult(...)` construction, return of `_load_docling`) | Pass `partial=partial, partial_reason=partial_reason` into the constructed `DoclingResult` |

### Fork 2 — partial-ingest signal (CROSS-LAYER: Python worker + .NET)

| File | Lines (current) | Change |
|---|---|---|
| `worker/app/services/loader.py` | 25-36 (`DoclingResult` dataclass) | Add `partial: bool = False` + `partial_reason: str \| None = None` fields |
| `worker/app/services/loader.py` | 54-81 (`load_documents`) | No signature change — `DoclingResult` objects already flow through untouched; only the `.pdf` dispatch path (`_load_docling`) constructs `DoclingResult` with the new fields populated (Fork 3 above); `.docx`/legacy-`.doc` paths keep `partial=False` default (this batch scopes the OR-merge fix to the PDF batched-conversion path only, per HIGH#2's actual failure mode — see Test Infra Improvement Notes for the docx/doc gap) |
| `worker/app/api/ingest.py` | 181-218 (`_run_pipeline`) | Change return type from bare `int` (chunk_count) to a small tuple/dataclass carrying `(chunk_count, partial, partial_reason)`; extract `partial`/`partial_reason` from the `DoclingResult` objects returned by `load_documents` (line 201) — note: `documents` is a `list[Any]`, so partial-ness must be aggregated across all returned `DoclingResult` objects (OR across the list, matching the OR-merge theme) |
| `worker/app/api/ingest.py` | 116-137 (`ingest_document`, success path) | Unpack the grown `_run_pipeline` return; pass `partial`/`partial_reason` into `IngestResponse(...)` construction |
| `worker/app/schemas/ingest.py` | 15-31 (`IngestResponse`) | Add `partial: bool = Field(False, ...)` + `partial_reason: str \| None = None` (additive, backward-compatible, snake_case — matches existing field style) |
| `Infrastructure/AiWorker/Contracts/IngestResult.cs` | 10-34 (`IngestResult` record) | Add `[JsonPropertyName("partial")] public bool Partial { get; init; }` + `[JsonPropertyName("partial_reason")] public string? PartialReason { get; init; }` |
| `Models/DocumentStatus.cs` | 7-20 (`DocumentStatus` enum) | Append `PartiallyIngested = 4` AFTER `Failed = 3` — never renumber existing values (per the file's own doc comment at line 5-6) |
| `Services/Documents/DocumentService.cs` | 230-240 (`IngestAsync`, success branch) | Change `if (result.IsSuccess)` block: when `result.IsSuccess && result.Partial` → call new `MarkPartiallyIngestedAsync` helper instead of `MarkReadyAsync`; when `result.IsSuccess && !result.Partial` → existing `MarkReadyAsync` path unchanged |
| `Services/Documents/DocumentService.cs` | 279-291 (`MarkReadyAsync`) | Add new sibling method `MarkPartiallyIngestedAsync(Guid id, int chunkCount, string? partialReason, CancellationToken ct)` mirroring `MarkReadyAsync`/`MarkFailedAsync` structure: sets `Status = DocumentStatus.PartiallyIngested`, `ChunkCount`, `ProcessedAt`, and stores `partialReason` into `ErrorMessage` (reuse the existing column — no new DB column, no migration) truncated the same way `MarkFailedAsync` truncates (line 295: `message.Length > 1000 ? message[..1000] : message`); broadcasts via existing `BroadcastAsync` helper |
| `Pages/Admin/Documents.cshtml` | 10-17 (`StatusChip` switch expression) | Add explicit arm: `DocumentStatus.PartiallyIngested => ("bg-amber-50 text-amber-700", "fa-solid fa-triangle-exclamation", "Nhập một phần"),` — placed before the `_ =>` default arm; amber styling distinct from red (Failed) and green (Ready) per delegation brief |
| `Pages/Admin/Documents.cshtml.cs` | 32-34, 58-64 (count properties + `OnGetAsync` counting logic) | Add `public int PartiallyIngestedCount { get; private set; }`; add `PartiallyIngestedCount = CountFor(DocumentStatus.PartiallyIngested);`. **Decision (resolving the brief's open question):** give `PartiallyIngested` its OWN stat card / count, NOT folded into `ProcessingCount` or `FailedCount` — it is neither "still processing" nor "hard failed," and folding it into either would hide the exact signal this fork exists to surface. Rationale ties directly to the anti-silent-loss theme of this whole batch. |
| `Pages/Admin/Documents.cshtml` | 52-90 (Stats Cards grid) | Add a 5th stat card for `PartiallyIngestedCount` (amber, matching the StatusChip color) — grid class changes from `lg:grid-cols-4` to `lg:grid-cols-5` (5 cards) OR the existing 4-card grid keeps `ProcessingCount`/`ReadyCount`/`FailedCount`/`TotalCount` and the 5th card wraps to a new row under the existing `sm:grid-cols-2` breakpoint — implementer's call at EXECUTE time on exact Tailwind grid class, not a design decision requiring a plan re-open; either rendering is visually acceptable |

---

## Public Contracts

- **`IngestResponse` (Python, wire schema)** — additive fields `partial: bool = False`,
  `partial_reason: str | None = None`. Existing `status`/`chunk_count`/`elapsed_ms`/`error_code`/
  `message` fields unchanged. Old .NET clients that don't read the new fields continue to work
  (Pydantic default + JSON omission-tolerant deserialization on the C# side via
  `System.Text.Json` — missing JSON properties bind to the C# default, `false`/`null`).
- **`IngestResult` (C#, deserialization target)** — additive properties, same backward-compat
  reasoning.
- **`DocumentStatus` enum** — new value `PartiallyIngested = 4` appended at the end. Existing values
  `0-3` unchanged → **no EF Core migration required** (enum is stored as `int`; existing rows keep
  their current int values; new value is simply a new valid int for future rows).
- **`DoclingResult` dataclass (internal, worker-only)** — additive fields with defaults; nothing
  outside `loader.py`/`chunker.py`/`ingest.py` constructs or pattern-matches on this dataclass by
  hand (confirmed via touchpoint scan above), so this is a safe internal contract change.
- **`Chunker.__init__` signature** — grows by one parameter (tokenizer or token-cap value). Exactly
  one call site (`main.py:86-89`) — confirmed via grep, no other construction sites exist.
- **`_convert_pdf_batched` return type** — grows from 2-tuple to 4-tuple. Exactly two call sites,
  both inside `_load_docling` in the same file (lines 345-347 digital pass, 369-371 OCR retry) —
  confirmed via Read above, no external callers.

No public HTTP API surface changes beyond the two new optional/defaulted JSON fields on
`IngestResponse`/`IngestResult` (additive, non-breaking).

---

## Blast Radius

**Risk class:** none of the 6 high-risk classes apply (no auth/identity, no billing/credits, no
schema-destructive migration — new enum value is additive with no migration, no public external API
contract break — additive-only, no deploy/container/gateway change, no secrets/trust-boundary
logic). This keeps the plan in the "lightly COMPLEX" tier rather than requiring `vc-security` /
`vc-predict` per the PLAN mode's auth/billing/secrets trigger.

**Files touched:** 9 files across 2 languages/runtimes.

| # | File | Language | Fork |
|---|---|---|---|
| 1 | `worker/app/services/chunker.py` | Python | Fork 1 + HIGH#1 |
| 2 | `worker/app/services/loader.py` | Python | Fork 3 + Fork 2 |
| 3 | `worker/app/main.py` | Python | Fork 1 |
| 4 | `worker/pyproject.toml` | Python (config) | Fork 1 |
| 5 | `worker/app/api/ingest.py` | Python | Fork 2 |
| 6 | `worker/app/schemas/ingest.py` | Python | Fork 2 |
| 7 | `Infrastructure/AiWorker/Contracts/IngestResult.cs` | C# | Fork 2 |
| 8 | `Models/DocumentStatus.cs` | C# | Fork 2 |
| 9 | `Services/Documents/DocumentService.cs` | C# | Fork 2 |
| 10 | `Pages/Admin/Documents.cshtml` | C# (Razor) | Fork 2 |
| 11 | `Pages/Admin/Documents.cshtml.cs` | C# | Fork 2 |

(11 files total — corrected count; table above lists 11 rows.)

**Packages/services:** `worker/` (Python FastAPI service) + root `.csproj` (.NET Razor Pages app).
No new services, containers, or runtime surfaces introduced. No new external dependency beyond
`transformers` (already transitively present — this pins it explicitly, not a new capability).

---

## Implementation Checklist — Fork 1: Token-Aware Chunking (worker-only)

1. **`worker/pyproject.toml`** — add `"transformers>=4.40.0",` to the `dependencies` list (after
   the `sentence-transformers`/`torch` block, lines 29-31, since it's conceptually adjacent —
   embedding-model-backend tooling).

2. **`worker/app/services/chunker.py` top of file** — add `import logging` (module-level, alongside
   existing `import re`); add `logger = logging.getLogger(__name__)` immediately after the imports
   (matches `loader.py`'s existing pattern at `loader.py:22`). This logger serves both Fork 1
   (tokenizer load info) and HIGH#1 (noise-filter DEBUG logging).

3. **`worker/app/services/chunker.py` `Chunker.__init__`** — add a `token_cap: int` parameter
   (default matches `embed_max_length`'s historical default of 1024, but the real value is always
   passed explicitly from `main.py`). Inside `__init__`, lazily import
   `from transformers import AutoTokenizer` and construct
   `self._tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")` — log
   `logger.info("chunker_tokenizer_ready model=BAAI/bge-m3")` after construction (mirrors
   `Embedder`'s `embedder_ready` log style). Store `self._token_cap = token_cap`.

4. **`worker/app/services/chunker.py` add a private token-count helper** — add a small method
   `def _count_tokens(self, text: str) -> int: return len(self._tokenizer.encode(text,
   add_special_tokens=False))`. Keep this as a bound method on `Chunker` (not a free function) since
   it needs `self._tokenizer`.

5. **`worker/app/services/chunker.py` `_split_table_block`** — change signature to accept a
   `token_counter: Callable[[str], int]` parameter (pass `self._count_tokens` at the call site in
   `_group_blocks`, step 6 below) instead of computing `len(text.split())` for `prefix_tokens`
   (line 307), `row_tokens` (line 314), and the `current_token_count` comparisons (lines 311, 315,
   319, 322). Replace each `len(X.split())` call in this function with `token_counter(X)`. Also
   replace the function's own `chunk_size` parameter meaning here specifically with the token cap
   (per Decision B) — rename the parameter at this call site conceptually to mean "token budget," or
   simply pass `self._token_cap` as the `chunk_size` argument when calling `_split_table_block` from
   `_group_blocks` (no rename required, just pass the right value — see step 6).

6. **`worker/app/services/chunker.py` `_group_blocks`** — thread `token_counter` through: change
   signature to accept it, pass `self._count_tokens` at the `Chunker.split`/`_recursive_split` call
   chain (the counter needs to flow from `Chunker.split` → `_split_markdown_by_headings` →
   `_recursive_split` → `_group_blocks` → `_split_table_block`; all four are currently free
   functions taking `fallback_splitter`/`metadata` params — add `token_counter` alongside them at
   each signature). At the `_split_table_block` call (line ~242), pass `self._token_cap` (not the
   word-based `chunk_size`) as the size-budget argument, per Decision B.

7. **`worker/app/services/chunker.py` `Chunker.split`** — after the existing `_merge_small_chunks`
   call (line 59) and before `return all_nodes` (line 60), add the final-emission safety check:
   iterate `all_nodes`, for each node call `self._count_tokens(node.get_content())`; if it exceeds
   `self._token_cap`, replace that single node in the list with the result of re-splitting it via
   the same row/line-boundary logic `_split_table_block` uses (Decision A/C) — call
   `_split_table_block(node.get_content().split("\n"), self._token_cap, node.metadata,
   token_counter=self._count_tokens)` and splice the resulting nodes in place of the original.
   Log `logger.warning("chunk_token_overflow_resplit tokens=%d cap=%d")` when this path triggers, so
   overflow frequency is observable.

8. **`worker/app/main.py`** — update the `Chunker(...)` construction at lines 86-89 to pass the new
   parameter: `app.state.chunker = Chunker(chunk_size=settings.chunk_size,
   chunk_overlap=settings.chunk_overlap, token_cap=settings.embed_max_length)`. No `config.py`
   change needed — `embed_max_length` already exists (line 36) and is exactly the value Decision B
   says to reuse.

**Test gates for Fork 1** (see Verification Evidence table for full detail): new pytest fixture —
oversized-table-chunk token-cap regression test; syntax/import check via `python -m py_compile`.

---

## Implementation Checklist — Fork 3: OCR Double-Pass OR-Merge (worker-only)

9. **`worker/app/services/loader.py` `_convert_pdf_batched`** — change the function's return type
   from `tuple[str, Any]` to `tuple[str, Any, int, list[tuple[int, int]]]`. Add a
   `failed_ranges: list[tuple[int, int]] = []` local list alongside the existing `failed_batches =
   0` counter (line 276). In both `except` branches (lines 296-308, the OOM branch and the generic
   exception branch), append `(start, end)` to `failed_ranges` in addition to incrementing
   `failed_batches`. Update both `return` statements: the small-PDF single-pass early return (line
   267-272) returns `(markdown, document, 0, [])` (no batching = no failure tracking needed —
   single-pass either fully succeeds or raises); the batched-loop return (line 310-318) returns
   `(merged, last_doc, failed_batches, failed_ranges)`.

10. **`worker/app/services/loader.py` `_load_docling`, PDF branch (lines 327-371)** — update the
    digital-pass call (line 345-347) to unpack the 4-tuple:
    `markdown_text, docling_doc, digital_failed_count, digital_failed_ranges =
    _convert_pdf_batched(...)`. Update the OCR-retry call (lines 369-371, inside the
    `_is_near_empty` branch) to unpack similarly:
    `markdown_text, docling_doc, ocr_failed_count, ocr_failed_ranges = _convert_pdf_batched(...)`
    (this REPLACES `markdown_text`/`docling_doc` — OCR pass output supersedes digital pass output
    for the final markdown, matching existing behavior at line 369-371).

11. **`worker/app/services/loader.py` OR-merge logic** — after both possible passes have run (i.e.
    right before the `except` blocks close and control reaches line 395's non-empty check),
    compute: `all_failed_ranges = digital_failed_ranges + (ocr_failed_ranges if ocr ran else [])`
    (OCR only runs conditionally inside the `_is_near_empty` branch, so guard accordingly — if OCR
    never ran, `ocr_failed_ranges` doesn't exist in scope; initialize
    `ocr_failed_ranges: list[tuple[int, int]] = []` before the `_is_near_empty` check so the
    variable always exists). Compute `partial = bool(all_failed_ranges)`. Build
    `partial_reason = f"Thiếu {len(all_failed_ranges)} nhóm trang: " + ", ".join(f"{s}-{e}" for s, e
    in all_failed_ranges) if partial else None` (Vietnamese-language reason string, matching the
    existing user-facing message style in this file, e.g. lines 358-362, 454-458).

12. **`worker/app/services/loader.py` `DoclingResult` construction (lines 404-410)** — pass
    `partial=partial, partial_reason=partial_reason` into the constructor call. For the non-PDF
    branch (`.docx`, line 372-376) and for `_load_text`/`_load_legacy_doc` paths, `partial`/
    `partial_reason` are not computed — they rely on the dataclass defaults (`False`/`None`) added
    in Fork 2 step 13 below, so no change needed at those call sites.

**Test gates for Fork 3**: new pytest fixture — `_convert_pdf_batched` with a mocked converter that
raises on one batch, asserting the returned failed-ranges list is non-empty and correctly bounded;
mocked-converter regression test for the OR-merge (both passes fail different ranges → merged
superset).

---

## Implementation Checklist — Fork 2: Partial-Ingest Signal (CROSS-LAYER)

13. **`worker/app/services/loader.py` `DoclingResult` dataclass (lines 25-36)** — add two fields
    with defaults: `partial: bool = False` and `partial_reason: str | None = None`. Since
    `dataclasses.dataclass` requires fields with defaults to come after fields without, and the
    existing fields (`text`, `docling_doc`, `metadata`) have no defaults, the two new fields must be
    appended at the end of the field list (which is what "additive" means here — no reordering of
    existing fields).

14. **`worker/app/api/ingest.py` `_run_pipeline` (lines 181-218)** — change the return type from
    `int` to a small named tuple or dataclass, e.g. add a lightweight local dataclass
    `@dataclasses.dataclass class _PipelineResult: chunk_count: int; partial: bool = False;
    partial_reason: str | None = None` near the top of the file (needs `import dataclasses` added).
    Inside the function body: after `documents = load_documents(...)` (line 201), aggregate
    partial-ness across the returned `documents` list (it's `list[Any]`, may contain multiple
    `DoclingResult` or plain `Document` objects — plain `Document` objects have no `.partial`
    attribute, so use `getattr(doc, "partial", False)`): `partial = any(getattr(d, "partial", False)
    for d in documents)`; collect reasons: `reasons = [getattr(d, "partial_reason", None) for d in
    documents if getattr(d, "partial_reason", None)]`; `partial_reason = "; ".join(reasons) if
    reasons else None`. Change the function's final `return vector_store.upsert_chunks(...)` (line
    210-216) to capture the chunk count into a local variable first, then
    `return _PipelineResult(chunk_count=chunk_count, partial=partial,
    partial_reason=partial_reason)`.

15. **`worker/app/api/ingest.py` `ingest_document`, success path (lines 116-137)** — update the
    `asyncio.to_thread(_run_pipeline, ...)` call's result unpacking: `pipeline_result =
    await asyncio.to_thread(...)`; then use `pipeline_result.chunk_count`,
    `pipeline_result.partial`, `pipeline_result.partial_reason` when constructing the
    `IngestResponse(...)` at line 131-136 — add `partial=pipeline_result.partial,
    partial_reason=pipeline_result.partial_reason` to the constructor call. Update the log line
    (124-127) to include `partial=%s` for observability.

16. **`worker/app/schemas/ingest.py` `IngestResponse` (lines 15-31)** — add
    `partial: bool = Field(False, description="True when some pages/batches were dropped during
    parsing (e.g. OCR retry still missing pages).")` and `partial_reason: str | None =
    Field(None, description="Human-readable (Vietnamese) summary of what was dropped, when partial
    is True.")`. Place after `chunk_count`/`elapsed_ms`, before the `error_code`/`message` failure
    fields (keeps success-path fields grouped together).

17. **`Infrastructure/AiWorker/Contracts/IngestResult.cs`** — add after `ChunkCount` (line 20):
    ```
    [JsonPropertyName("partial")]
    public bool Partial { get; init; }

    [JsonPropertyName("partial_reason")]
    public string? PartialReason { get; init; }
    ```

18. **`Models/DocumentStatus.cs`** — append after `Failed = 3` (line 19), before the closing brace:
    ```
    /// <summary>Ingestion succeeded but some pages/content were dropped (e.g. OCR retry
    /// still missing pages). See <c>ErrorMessage</c> for details.</summary>
    PartiallyIngested = 4,
    ```
    Do NOT renumber `Pending`/`Processing`/`Ready`/`Failed` — the file's own doc comment (line 5-6)
    forbids this (stored as `int` in SQL).

19. **`Services/Documents/DocumentService.cs` `IngestAsync` success branch (lines 230-240)** —
    change:
    ```csharp
    if (result.IsSuccess && result.Partial)
    {
        await MarkPartiallyIngestedAsync(document.Id, result.ChunkCount, result.PartialReason, cancellationToken);
        _ = _audit.LogAsync(
            "doc.ingest_partial", "doc", LogSeverity.Warn,
            resourceType: nameof(Document),
            resourceId:   document.Id.ToString(),
            overrideUserId:       document.UploaderId,
            overrideDepartmentId: document.DepartmentId,
            details: new { chunkCount = result.ChunkCount, elapsedMs = result.ElapsedMs, partialReason = result.PartialReason });
    }
    else if (result.IsSuccess)
    {
        await MarkReadyAsync(document.Id, result.ChunkCount, cancellationToken);
        _ = _audit.LogAsync(/* existing doc.ingest_success block, unchanged */);
    }
    else
    {
        /* existing MarkFailedAsync + doc.ingest_failed block, unchanged */
    }
    ```
    Note: `LogSeverity.Warn` (the actual enum member — see `Models/LogSeverity.cs`: `Debug, Info,
    Warn, Error`. VALIDATE corrected this from `LogSeverity.Warning` to `LogSeverity.Warn`; the
    `all-context.md` prose "Info, Warning, Error" was informal shorthand, not the exact member
    name. Confirmed via source read + 5 existing call-site greps, e.g.
    `Controllers/Api/DocumentsController.cs:340`, `Pages/Account/Login.cshtml.cs:86`.)

20. **`Services/Documents/DocumentService.cs` new helper method** — add after `MarkReadyAsync`
    (after line 291), mirroring its structure:
    ```csharp
    private async Task<int> MarkPartiallyIngestedAsync(
        Guid id, int chunkCount, string? partialReason, CancellationToken ct)
    {
        var truncated = partialReason is { Length: > 1000 } ? partialReason[..1000] : partialReason;
        var n = await _db.Documents
            .Where(d => d.Id == id)
            .ExecuteUpdateAsync(s => s
                .SetProperty(d => d.Status,       DocumentStatus.PartiallyIngested)
                .SetProperty(d => d.ChunkCount,   chunkCount)
                .SetProperty(d => d.ProcessedAt,  DateTime.UtcNow)
                .SetProperty(d => d.ErrorMessage, truncated),
                ct);
        await BroadcastAsync(id, DocumentStatus.PartiallyIngested);
        return n;
    }
    ```
    Reuses the existing `ErrorMessage` column — no new DB column, no EF Core migration needed
    (confirmed: `Document` model already has `ErrorMessage`, used identically by `MarkFailedAsync`).

21. **`Pages/Admin/Documents.cshtml` `StatusChip` (lines 10-17)** — add before the `_ =>` default
    arm:
    ```csharp
    DocumentStatus.PartiallyIngested => ("bg-amber-50 text-amber-700", "fa-solid fa-triangle-exclamation", "Nhập một phần"),
    ```

22. **`Pages/Admin/Documents.cshtml.cs`** — add property `public int PartiallyIngestedCount { get;
    private set; }` near line 34 (after `FailedCount`); add
    `PartiallyIngestedCount = CountFor(DocumentStatus.PartiallyIngested);` near line 64 (after the
    existing `FailedCount` assignment). **Decision:** own count bucket, not folded into
    Processing/Failed (see Touchpoints table rationale).

23. **`Pages/Admin/Documents.cshtml` Stats Cards (lines 52-90)** — add a 5th stat card for
    `PartiallyIngestedCount`, amber-themed to match the new `StatusChip` color
    (`bg-amber-50`/`text-amber-500` icon wrapper, following the existing 4-card pattern at lines
    63-71 as the template). Grid class adjustment left to implementer at EXECUTE time (either widen
    to `lg:grid-cols-5` or let the 5th card wrap under `sm:grid-cols-2`) — purely a Tailwind layout
    choice, not a design decision requiring plan re-open.

**Test gates for Fork 2**: mocked-converter pytest asserting `partial`/`partial_reason` propagate
from `DoclingResult` through `_run_pipeline` to `IngestResponse`; `dotnet build` compiles cleanly
with the new enum value, contract fields, and Razor page changes; manual smoke check that a
document forced into `PartiallyIngested` status renders the correct chip/count on `/Admin/Documents`.

---

## Implementation Checklist — HIGH#1: Noise-Filter Tightening (worker-only, not a fork)

24. **`worker/app/services/chunker.py` `_SIGNATURE_RE` (lines 344-346)** — tighten to require a
    stronger signal than "any 2-6-word Titlecase phrase." Concretely: require the phrase to ALSO
    match a known signature-block vocabulary (reuse the existing `_NOISE_PATTERNS` title terms —
    GIÁM ĐỐC / PHÓ GIÁM ĐỐC / NGƯỜI LẬP as anchoring words) OR require positional context (this
    function only receives already-isolated small chunks post-split, so true positional
    end-of-document detection isn't available at this call site without larger refactor — scope
    this to the vocabulary-anchor approach only, per the "keep this scoped" instruction in the
    brief). Revised pattern requires the phrase to contain at least one of the known signature
    title words (case-insensitive) rather than matching bare Titlecase shape alone:
    ```python
    _SIGNATURE_TITLE_WORDS = r"(?:GIÁM\s*ĐỐC|TRƯỞNG|PHÓ|CHỦ\s*TỊCH|BÍ\s*THƯ)"
    _SIGNATURE_RE = re.compile(
        rf"^[A-ZÀ-Ỹ][a-zà-ỹ]+(\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){{1,5}}$"
    )
    ```
    plus an additional guard inside `_is_noise` (step 25) rather than baking the vocabulary into the
    regex alone — see step 25 for the actual gating logic (regex alone can't easily express "OR
    contains a title word" cleanly combined with the existing Titlecase shape check, so the guard
    moves into the calling function).

25. **`worker/app/services/chunker.py` `_is_noise` (lines 349-364)** — change the signature-check
    branch (line 359-360: `if words <= 4 and _SIGNATURE_RE.match(stripped): return True`) to also
    require the text contain a recognizable title/role word:
    ```python
    _TITLE_WORD_RE = re.compile(r"GIÁM\s*ĐỐC|TRƯỞNG|PHÓ|CHỦ\s*TỊCH|BÍ\s*THƯ|KT\.", re.IGNORECASE)
    ...
    if words <= 4 and _SIGNATURE_RE.match(stripped) and _TITLE_WORD_RE.search(stripped):
        logger.debug("noise_filtered_signature text=%r", stripped)
        return True
    ```
    This keeps the Titlecase shape check (still useful — avoids matching e.g. all-lowercase prose)
    but now ALSO requires a recognizable title/role word, so ordinary proper nouns like "Ủy Ban Nhân
    Dân" (no title word) are no longer silently deleted. Add a `logger.debug(...)` call at every
    `return True` branch in `_is_noise` (the `_NOISE_PATTERNS.match` branch at line 357-358 too, and
    the final digit-strip branch at 361-363) so filtered chunks are observable at DEBUG level —
    satisfies the brief's "add DEBUG logging of filtered chunks" requirement across all noise-filter
    paths, not just the signature one.

26. **`worker/app/services/chunker.py` digit-strip bug (debt #9, line 361)** — current:
    `clean = re.sub(r"[,.\-–—;:()=\d\s]", "", stripped)`. This strips digits (`\d`) before counting
    remaining length, which means a legitimate reference number like `"1202/QĐ-BKHCNCGCN"` strips
    down to just `"QĐBKHCNCGCN"` — wait, that's 11 chars, still passes `len(clean) < 3`. Re-checking
    the actual failure: for a purely-numeric-with-punctuation reference like `"1202/2024/QĐ-UBND"`,
    stripping digits and punctuation leaves `"QĐUBND"` (6 chars) — also passes. The real failure
    case is shorter references like `"12/QĐ"` → strips to `"QĐ"` (2 chars) → `len(clean) < 3` → TRUE
    → filtered as noise, even though `"12/QĐ"` is a legitimate (if terse) reference fragment. Fix:
    exclude `\d` from the strip pattern so digits count toward the "is this real content" length
    check: `clean = re.sub(r"[,.\-–—;:()=\s]", "", stripped)` (removed `\d` from the character
    class). This makes `"12/QĐ"` strip to `"12QĐ"` (4 chars) → passes the `< 3` check → correctly
    NOT filtered as noise. Add `logger.debug("noise_filtered_symbol_soup text=%r", stripped)` at
    this branch's `return True` (line 362-363) per the DEBUG-logging requirement.

**Test gates for HIGH#1**: new pytest fixture — Vietnamese Titlecase heading NOT filtered (e.g. "Ủy
Ban Nhân Dân Tỉnh Lâm Đồng" survives `_is_noise`); existing true-positive signature block (e.g. "KT.
GIÁM ĐỐC\nNguyễn Văn A") still correctly filtered; reference-number fixture (e.g. "1202/QĐ-BKHCNCGCN"
and "12/QĐ") NOT filtered.

---

## Acceptance Criteria

Testable, observable "done" conditions for this batch (maps 1:1 to the four checklist sections):

1. **Fork 1 (token-aware chunking):** a synthetic oversized markdown table fed through `Chunker.split`
   produces zero `TextNode`s whose exact bge-m3 tokenizer count exceeds `settings.embed_max_length`
   (1024) — proven by the new `test_oversized_table_chunk_respects_token_cap` fixture.
2. **Fork 3 (OCR OR-merge):** `_convert_pdf_batched` returns a non-empty `failed_ranges` list when any
   page batch is skipped (mocked OOM/exception), and the OR-merge across digital + OCR-retry passes
   unions both passes' failed ranges rather than overwriting — proven by the two new
   `test_loader_partial.py` fixtures.
3. **Fork 2 (partial-ingest signal):** a `DoclingResult` with `partial=True` set by Fork 3 survives the
   full worker→`.NET` hop and results in `DocumentStatus.PartiallyIngested` on the row, with the admin
   `/Admin/Documents` page rendering the amber "Nhập một phần" chip and a non-zero
   `PartiallyIngestedCount` — proven by the `test_ingest_partial_propagation` fixture (worker hop) plus
   the manual smoke check (UI hop).
4. **HIGH#1 (noise-filter tightening):** ordinary Vietnamese Titlecase headings/proper nouns
   (e.g. "Ủy Ban Nhân Dân Tỉnh Lâm Đồng") are no longer filtered as noise; true-positive signature
   blocks are still filtered; legitimate short reference numbers (e.g. "12/QĐ") are no longer
   filtered — proven by the three `test_chunker_noise_filter.py` fixtures.
5. **No regression:** a full end-to-end ingest of a real, fully-parseable sample PDF from
   `data/lamdong_pdf/` still results in `DocumentStatus.Ready` (not `PartiallyIngested`) — proven by
   the manual regression smoke check in Verification Evidence.
6. **Build health:** `dotnet build` and `python -m py_compile` (all 5 touched Python files) both exit
   0 after all four sections are implemented.

## Phase Completion Rules

This is a SIMPLE/lightly-COMPLEX single-session plan — no phase program, so there is only one
"phase" (this plan) and it follows the standard RIPER-5 gate, not the multi-phase program loop:

- Status moves from `CODE DONE` (all 26 checklist items implemented, `dotnet build` +
  `py_compile` green) to `VERIFIED` only after the Hybrid-tier pytest fixtures listed in
  Verification Evidence are green AND the two Agent-Probe manual smoke checks have been performed
  and confirmed by the user (per this repo's "code-only completion is CODE DONE, not VERIFIED" rule).
- Recommended EXECUTE order is Fork 3 → Fork 2 → Fork 1 → HIGH#1 (see Dependencies section) — each
  section can be committed independently once its own gates are green, or all four can land in one
  commit since they share the same uncommitted worker diff boundary.
- Do not mark this plan `✅ VERIFIED` in any future update without explicit user confirmation of the
  manual smoke checks (UI chip rendering, full-success-path regression) per this repo's
  user-confirmation-for-VERIFIED convention.

## Verification Evidence

Worker has **zero automated tests today** (confirmed: no `pytest` in `worker/.venv/site-packages`,
no test files under `worker/app/`, `process/context/tests/all-tests.md` explicitly lists this as a
Known Gap). This plan both fixes bugs AND stands up the first pytest fixtures per the debt file's
test-priority order. No `.sln`/test project exists on the .NET side either — `dotnet build` is the
only automated gate available there today.

| Gate / Scenario | Strategy | Proves SPEC criterion |
|---|---|---|
| `python -m py_compile worker/app/services/chunker.py worker/app/services/loader.py worker/app/api/ingest.py worker/app/schemas/ingest.py worker/app/main.py` | Fully-Automated | All 5 touched Python files are syntactically valid |
| `cd worker && python -c "from app.services.chunker import Chunker; from app.services.loader import DoclingResult; from app.schemas.ingest import IngestResponse"` (import smoke check — requires `pip install -e .` with new `transformers` pin first) | Fully-Automated | New imports (`transformers.AutoTokenizer`, new dataclass fields, new Pydantic fields) resolve without `ImportError`/`AttributeError` at module load time |
| `dotnet build` (from repo root, `chatbot.csproj`) | Fully-Automated | New enum value, new record properties, new Razor Page arms, new `DocumentService` method all compile cleanly with no C# errors |
| New fixture: `worker/app/services/test_chunker_token_cap.py::test_oversized_table_chunk_respects_token_cap` — construct a `Chunker` with a small `token_cap` (e.g. 50), feed a synthetic markdown table with rows guaranteed to exceed 50 tokens combined, assert every emitted `TextNode`'s exact tokenizer count is `<= token_cap` | Hybrid — requires `transformers`/tokenizer download (network on first run, cached after); precondition: `pip install -e .` includes new `transformers` pin | Fork 1 Decision A/B: table chunks never silently exceed the real embedder token cap |
| New fixture: `test_chunker_noise_filter.py::test_vietnamese_heading_not_filtered` — assert `_is_noise("Ủy Ban Nhân Dân Tỉnh Lâm Đồng")` returns `False` | Fully-Automated (pure function, no model/network dependency) | HIGH#1: ordinary Vietnamese Titlecase headings/proper nouns no longer silently deleted |
| New fixture: `test_chunker_noise_filter.py::test_true_signature_still_filtered` — assert `_is_noise("KT. GIÁM ĐỐC\nNguyễn Văn A")`-style true-positive signature block still returns `True` | Fully-Automated | HIGH#1 fix does not regress the original true-positive detection this filter exists for |
| New fixture: `test_chunker_noise_filter.py::test_reference_number_not_filtered` — assert `_is_noise("1202/QĐ-BKHCNCGCN")` and `_is_noise("12/QĐ")` both return `False` | Fully-Automated | Debt #9 digit-strip bug fixed — legit reference numbers survive |
| New fixture: `worker/app/services/test_loader_partial.py::test_convert_pdf_batched_reports_failed_ranges` — mock `converter.convert` to raise on one page-batch call, assert returned `failed_batches > 0` and `failed_ranges` contains the expected `(start, end)` tuple | Hybrid — requires mocking Docling's `DocumentConverter`; no real PDF/model needed, pure mock | Fork 3: dropped page batches are surfaced, not silently discarded |
| New fixture: `test_loader_partial.py::test_or_merge_across_digital_and_ocr_passes` — mock both the digital-pass and OCR-retry `_convert_pdf_batched` calls to each report a different failed range, assert the final `DoclingResult.partial_reason` mentions both ranges (OR-merge, not overwrite) | Hybrid — same mocking precondition | Fork 3: OR-merge correctly unions failure signals from both passes |
| New fixture: `worker/app/api/test_ingest_partial_propagation.py::test_partial_flag_propagates_to_ingest_response` — mock `load_documents` to return a `DoclingResult` with `partial=True, partial_reason="test"`, call `_run_pipeline` directly (mocking `chunker`/`embedder`/`vector_store` too), assert the returned result's `partial`/`partial_reason` match | Hybrid — requires mocking 3 collaborators; deterministic once mocked | Fork 2: partial signal survives the worker-side `_run_pipeline` → `IngestResponse` hop |
| Manual smoke: force a `DocumentStatus.PartiallyIngested` row via direct DB update (or a temporary debug branch), load `/Admin/Documents`, confirm the amber chip renders with label "Nhập một phần" and the new stat card shows count ≥ 1 | Agent-Probe | Fork 2: UI visibly surfaces the partial status — the actual user-facing goal of this fork |
| Manual smoke: upload one real sample PDF from `data/lamdong_pdf/` end-to-end through the full pipeline (worker running, .NET running) and confirm `Ready` status still renders correctly (regression check — full-success path unaffected) | Agent-Probe | No regression: fully-successful ingests are NOT reclassified as partial by the new logic |
| Known-gap: `.docx`/legacy-`.doc` paths never populate `partial`/`partial_reason` (Fork 3 scope is PDF-batched-conversion only, per the touchpoints note) | Known-Gap | Explicitly out of scope for this batch — DOCX/DOC parsing has no page-batching mechanism to fail partially in the same way; backlog candidate if DOCX partial-failure modes are found later |

---

## Test Infra Improvement Notes

- **`pytest` is not yet installed in `worker/.venv`** — this plan is the first to add pytest
  fixtures to the worker. EXECUTE must add `pytest>=8.0.0` to `worker/pyproject.toml`'s
  dependencies (or a new `[project.optional-dependencies].test` group) and run `pip install -e
  ".[test]"` (or equivalent) before the new fixtures can run. This was not called out as a locked
  decision in INNOVATE, so EXECUTE should add it as a natural prerequisite of "write pytest
  fixtures," not as new scope requiring a plan revision.
- **No `worker/app/services/__init__.py`-adjacent test directory convention exists yet** — this plan
  establishes `worker/app/services/test_chunker_token_cap.py`,
  `worker/app/services/test_chunker_noise_filter.py`, `worker/app/services/test_loader_partial.py`,
  and `worker/app/api/test_ingest_partial_propagation.py` as colocated test files (matching the
  `all-tests.md` guidance: "runner: pytest from within worker/"). Future test-infra work could
  consolidate these into a dedicated `worker/tests/` tree, but colocated-with-source is consistent
  with the debt file's own suggested fixture names and avoids inventing new structure mid-fix-batch.
- **No `.sln`/xUnit project exists for the .NET side** — `dotnet build` is a compile-only gate, not
  a behavioral test. The `MarkPartiallyIngestedAsync` helper and the `StatusChip`/count-bucket logic
  have zero automated behavioral coverage on the .NET side after this plan; the Agent-Probe manual
  smoke checks in Verification Evidence are the only proof. If/when an xUnit project is added,
  `DocumentService.IngestAsync`'s branch logic (Ready / PartiallyIngested / Failed) is the highest-
  value first test target — noted here for that future work, not actioned in this plan.
- **DOCX/legacy-DOC partial-failure detection is a known gap** (see Verification Evidence table) —
  if Docling's DOCX path ever exhibits an analogous silent-drop failure mode, a follow-up plan would
  need to extend `partial`/`partial_reason` population beyond the PDF-only scope set here.

---

## Dependencies

- Fork 1 depends on nothing else in this batch — can be implemented/tested independently.
- Fork 3 depends on nothing else in this batch — can be implemented/tested independently.
- Fork 2 DEPENDS on Fork 3 (needs `DoclingResult.partial`/`partial_reason` populated by Fork 3's
  OR-merge logic to have anything meaningful to propagate) but Fork 2's dataclass field addition
  (step 13) and downstream plumbing (steps 14-23) can be scaffolded in parallel with Fork 3's
  internal logic — just sequence Fork 3 before Fork 2 at EXECUTE time within this single plan, or
  implement Fork 2's plumbing with `partial=False` defaults first and wire in Fork 3's real signal
  last. **Recommended EXECUTE order: Fork 3 → Fork 2 → Fork 1 → HIGH#1** (Fork 3 before Fork 2
  because of the real dependency; Fork 1 and HIGH#1 are independent and can go in either position,
  placed last here only because they're lower-risk/higher-isolation).
- HIGH#1 depends on nothing else — fully independent, touches only `_is_noise`/`_SIGNATURE_RE`.
- No dependency on the AI Settings dashboard work (separate feature, already shipped per
  `all-context.md` Current Active Work) or any other in-flight plan — no other active plans exist in
  `process/general-plans/active/` or `process/features/*/active/` as of this writing (confirmed via
  directory scan).

## Risks

| Risk | Class | Mitigation |
|---|---|---|
| `transformers.AutoTokenizer.from_pretrained("BAAI/bge-m3")` triggers a network download on first call (few MB, not the 2GB model) | Low — one-time cost, cached by HuggingFace cache dir after first run | Document in commit message; not a blocker for laptop dev environment which already downloads bge-m3 weights via `Embedder` |
| Threading `token_counter` through 4 nested functions (`_split_markdown_by_headings` → `_recursive_split` → `_group_blocks` → `_split_table_block`) increases signature surface area | Low — mechanical, no behavior ambiguity | Keep the counter as a plain `Callable[[str], int]` parameter throughout — do not introduce a new class/protocol for this |
| Fork 2's `partial` aggregation across a `list[Any]` of mixed `DoclingResult`/`Document` objects could silently miss non-`DoclingResult` partial-ness if a future loader path introduces its own partial concept without updating `_run_pipeline`'s `getattr` aggregation | Low — defensive `getattr(d, "partial", False)` already handles today's mixed-type list correctly | Note in code comment at the aggregation site; no action needed today |
| .NET `Partial`/`PartialReason` properties deserialize incorrectly if worker and .NET are deployed out of sync (old worker without new fields talking to new .NET code) | Low — additive/backward-compatible by design; missing JSON fields bind to C# defaults (`false`/`null`) | No mitigation needed beyond the additive design itself; already accounted for in Public Contracts section |
| `PartiallyIngested` status is new — any existing code that does exhaustive status handling (switch statements, etc.) elsewhere in the codebase could silently fall through to a default case instead of handling the new status explicitly | Medium — silent UI gaps possible outside `Documents.cshtml` | EXECUTE must grep for all `DocumentStatus` switch/pattern-match sites beyond the two touched in this plan (`Documents.cshtml`'s `StatusChip`, `Documents.cshtml.cs`'s counting) before considering Fork 2 done — add this as an explicit EXECUTE-time grep step, not just the two sites already identified |

## Backwards Compatibility

- All wire-format changes (`IngestResponse`, `IngestResult`) are additive with defaults —
  old-worker/new-.NET and new-worker/old-.NET combinations both degrade gracefully (partial signal
  simply reads as `false`/`null` on the older side).
- `DocumentStatus` enum append is additive — no migration, no renumbering, existing rows unaffected.
- `Chunker.__init__` signature change is NOT backward compatible in the sense that any external
  caller passing only `(chunk_size, chunk_overlap)` positionally would need the new param — but the
  ONE confirmed call site (`main.py:86-89`) is updated in this same plan (step 8), and no other
  construction sites exist (confirmed via grep during RESEARCH). Safe to treat as internal-only.
- `_convert_pdf_batched` return-type change is internal-only (both call sites are in the same file,
  updated in this same plan) — no external contract impact.

---

## Resume and Execution Handoff

1. **Selected plan file path:** `process/general-plans/active/worker-precommit-fixes_06-07-26/worker-precommit-fixes_PLAN_06-07-26.md` (this file)
2. **Last completed phase or step:** PLAN — this document. RESEARCH and INNOVATE are complete and
   locked (per delegation brief); no separate SPEC or research artifact exists for this batch (the
   backlog debt file `process/general-plans/backlog/code-debt_worker-audit_06-07-26.md` served as
   the RESEARCH input).
3. **Validate-contract status:** pending (placeholder below — `vc-validate-agent` writes this
   section before EXECUTE)
4. **Supporting context files loaded:** `process/context/all-context.md` (root router),
   `process/context/tests/all-tests.md` (test routing — confirmed zero worker tests, no pytest
   installed, `dotnet build` only .NET gate), `process/general-plans/backlog/code-debt_worker-audit_06-07-26.md` (source debt/scope)
5. **Next step for a fresh agent picking up mid-execution:** Run `ENTER VALIDATE MODE` against this
   plan file first. If execution was interrupted mid-batch, check which of the 4 sections (Fork 3,
   Fork 2, Fork 1, HIGH#1) has committed changes in the worker diff via `git diff --stat
   worker/app/services/chunker.py worker/app/services/loader.py worker/app/main.py
   worker/app/api/ingest.py worker/app/schemas/ingest.py Infrastructure/AiWorker/Contracts/IngestResult.cs
   Models/DocumentStatus.cs Services/Documents/DocumentService.cs Pages/Admin/Documents.cshtml
   Pages/Admin/Documents.cshtml.cs worker/pyproject.toml` and resume at the first incomplete
   checklist item, respecting the recommended EXECUTE order (Fork 3 → Fork 2 → Fork 1 → HIGH#1).

---

## Validate Contract

Status: CONDITIONAL
Date: 06-07-26
date: 2026-07-06
generated-by: outer-pvl

Parallel strategy: sequential
Rationale: single self-contained plan, no phase program, no independent directions requiring fan-out (score 1/7)

Test gates (C3 5-column table):

| criterion id | behavior | strategy | proving test | gap-resolution |
|---|---|---|---|---|
| AC1 | Fork 1: oversized table chunk never exceeds bge-m3 token cap (1024) | Fully-Automated | `python -m py_compile worker/app/services/chunker.py worker/app/services/loader.py worker/app/api/ingest.py worker/app/schemas/ingest.py worker/app/main.py` (syntax gate) | A |
| AC1 | Fork 1: exact token-cap enforcement at table-split + final-emission | Hybrid | `cd worker && pytest app/services/test_chunker_token_cap.py::test_oversized_table_chunk_respects_token_cap` — precondition: `pip install pytest` into `worker/.venv` (pytest confirmed absent; `transformers` confirmed already present, no network/model download needed for tokenizer) | B |
| AC2 | Fork 3: `_convert_pdf_batched` surfaces failed page ranges instead of discarding them | Hybrid | `cd worker && pytest app/services/test_loader_partial.py::test_convert_pdf_batched_reports_failed_ranges` — precondition: pytest installed; mocked converter, no real PDF needed | B |
| AC2 | Fork 3: OR-merge unions digital-pass + OCR-retry-pass failed ranges | Hybrid | `cd worker && pytest app/services/test_loader_partial.py::test_or_merge_across_digital_and_ocr_passes` — precondition: pytest installed | B |
| AC3 | Fork 2: `partial`/`partial_reason` survive worker `_run_pipeline` → `IngestResponse` hop | Hybrid | `cd worker && pytest app/api/test_ingest_partial_propagation.py::test_partial_flag_propagates_to_ingest_response` — precondition: pytest installed, mocks chunker/embedder/vector_store | B |
| AC3 | Fork 2: cross-layer contract + enum + Razor UI compile cleanly | Fully-Automated | `dotnet build` (confirmed green on baseline pre-fix files) | A |
| AC3 | Fork 2: `PartiallyIngested` status renders correct amber chip + non-zero stat count on `/Admin/Documents` | Agent-Probe | Manual smoke: force a `PartiallyIngested` row via direct DB update, load `/Admin/Documents`, confirm chip label "Nhập một phần" + stat card ≥ 1 | D |
| AC3 | Fork 2: `.docx`/legacy-`.doc` ingest paths never populate `partial`/`partial_reason` (out of scope, PDF-batching-only mechanism) | Known-Gap | — | D |
| AC4 | HIGH#1: Vietnamese Titlecase headings/proper nouns no longer filtered as noise | Fully-Automated | `cd worker && pytest app/services/test_chunker_noise_filter.py::test_vietnamese_heading_not_filtered` (pure function, no model/network dep) — precondition: pytest installed | B |
| AC4 | HIGH#1: true-positive signature blocks still filtered (no regression) | Fully-Automated | `cd worker && pytest app/services/test_chunker_noise_filter.py::test_true_signature_still_filtered` | B |
| AC4 | HIGH#1: legitimate short reference numbers (e.g. "12/QĐ") no longer filtered | Fully-Automated | `cd worker && pytest app/services/test_chunker_noise_filter.py::test_reference_number_not_filtered` | B |
| AC5 | No regression: fully-successful PDF ingest still yields `Ready`, not `PartiallyIngested` | Agent-Probe | Manual smoke: upload one real sample PDF from `data/lamdong_pdf/` end-to-end (worker + .NET running), confirm `Ready` status renders | D |
| AC6 | Build health: both runtimes compile/parse cleanly after all 4 sections land | Fully-Automated | `python -m py_compile` (5 files) + `dotnet build` — both confirmed green on current baseline (pre-fix state); must remain green post-fix | A |

gap-resolution legend:
- A — proven now (gate passes in this cycle, on current baseline)
- B — fixed in this plan (new pytest fixture added by this plan's checklist; requires `pip install pytest` as a listed EXECUTE prerequisite, not a new scope item)
- C — deferred to a named later phase/plan
- D — backlog test-building stub (named residual; keep-active; continue) — Agent-Probe manual smokes and the docx/doc Known-Gap fall here

C-4 reconciliation: `strategy:` column carries only Fully-Automated / Hybrid / Agent-Probe. Known-Gap (docx/doc partial detection) is a named residual row via gap-resolution D, never a proving strategy.

Legacy line form:
- Fork 1 (chunker.py token-aware split): Fully-automated: `python -m py_compile ...` | Hybrid: `pytest test_chunker_token_cap.py` (precondition: pytest installed) | known-gap: none
- Fork 3 (loader.py OCR OR-merge): Hybrid: `pytest test_loader_partial.py` (precondition: pytest installed, mocked converter)
- Fork 2 (cross-layer partial-ingest): Fully-automated: `dotnet build` | Hybrid: `pytest test_ingest_partial_propagation.py` | agent-probe: UI chip smoke + full-success regression smoke | known-gap: docx/doc paths (documented, PDF-only scope)
- HIGH#1 (noise-filter): Fully-automated: 3 pure-function pytest fixtures (no model/network dependency)

Dimension findings:
- Infra fit: PASS — no container/deploy/runtime surface touched; `dotnet build` confirmed green on baseline; worker/.NET boundary unchanged
- Test coverage: CONCERN — pytest confirmed absent from `worker/.venv` (plan already documents this as a required EXECUTE-time prerequisite, not a newly-discovered gap); accepted as a listed setup step
- Breaking changes: PASS — enum append additive (no EF migration); `IngestResponse`/`IngestResult` fields additive+defaulted; confirmed only one near-exhaustive switch (`StatusChip` in `Pages/Admin/Documents.cshtml:10-17`) and it has a `_ =>` default arm; `Chunker.__init__` (1 call site, `main.py:86`) and `_convert_pdf_batched` (2 call sites, both `loader.py:345`/`369`) confirmed via grep — no other construction/call sites exist
- Security surface: PASS — no auth/identity, billing/credits, schema-destructive migration, public external API break, deploy/container/gateway, or secrets/trust-boundary surface; risk-class scan confirms none of the 6 high-risk classes apply
- Section Fork 1 (token-aware chunking): PASS — `transformers.AutoTokenizer` confirmed already importable in `worker/.venv` (v5.10.1, transitive via sentence-transformers; tokenizer-only load, not the 2GB model weights) — no live/billed feasibility probe needed, this is a local package-resolution fact, not an untested runtime behavior
- Section Fork 3 (OCR double-pass OR-merge): PASS — `_convert_pdf_batched` current 2-tuple return and both call sites (lines 345, 369 in `_load_docling`) match the plan's described locations exactly; growing to a 4-tuple is mechanically clean, no other callers exist
- Section Fork 2 (partial-ingest signal, cross-layer): CONCERN (RESOLVED) — checklist step 19's code snippet used `LogSeverity.Warning`, but the actual enum member is `LogSeverity.Warn` (`Models/LogSeverity.cs`: `Debug, Info, Warn, Error`); would have failed `dotnet build`. Fixed in plan text at V6 (step 19 snippet + note corrected to `LogSeverity.Warn`, matching the 5 existing call sites in the codebase, e.g. `Controllers/Api/DocumentsController.cs:340`). Everything else in this section (enum append, `DocumentService` branch structure, `ErrorMessage` column reuse, `StatusChip` default-arm safety, `IsSuccess`/`Partial` ordering) verified correct against current source.
- Section HIGH#1 (noise-filter tightening): PASS — `_SIGNATURE_RE`, `_is_noise`, and the digit-strip line all match the current file exactly (chunker.py lines 344-364); tightening logic and digit-strip fix are both mechanically sound against the real regex/function bodies

Open gaps:
- pytest not yet installed in `worker/.venv` — documented in plan's own "Test Infra Improvement Notes" as a required EXECUTE-time prerequisite (add `pytest>=8.0.0` to `worker/pyproject.toml` test deps, `pip install -e ".[test]"` or equivalent) — not treated as a blocking gap since it's already scoped as setup, not new work
- `.docx`/legacy-`.doc` partial-failure detection: known-gap, documented as PDF-batching-only scope in Verification Evidence and Test Infra Improvement Notes — no backlog artifact needed beyond the existing in-plan documentation (explicitly acknowledged, not silently dropped)

What this coverage does NOT prove:
- The Fully-Automated py_compile/dotnet build gates only prove syntactic/compile validity — they do NOT prove the token-cap math is correct, the OR-merge logic is correct, or the noise-filter regex behaves correctly on real Vietnamese text. Those are proven only by the Hybrid pytest fixtures (gap-resolution B — new tests, not yet written/run).
- The Hybrid pytest fixtures (once written per this plan) prove unit-level correctness with mocked/synthetic inputs — they do NOT prove behavior against a real production-scale Vietnamese government PDF/DOCX corpus, real Docling OCR output variance, or real bge-m3 tokenizer edge cases beyond the synthetic fixtures' scope.
- The Agent-Probe manual smokes (UI chip rendering, full-success regression) prove one-time human-observed correctness at EXECUTE time — they do NOT constitute a repeatable automated regression gate; if the UI or ingest pipeline changes again later, these checks must be re-run manually, not inferred from this pass.
- The Known-Gap row (docx/doc partial detection) proves nothing — it is an explicit scope exclusion, not a tested-and-passing behavior.
- No test proves multi-document-type interaction (e.g. a mixed-batch upload) or concurrent-ingest behavior under the new partial-status branch — out of scope for this plan, not called out as a separate known-gap since concurrent ingest is already serialized via `_INGEST_LOCK` (unchanged by this plan).

Gate: CONDITIONAL (1 concern found — LogSeverity.Warning to Warn typo — corrected in plan text at V6; no unresolved FAILs; pytest-absence concern accepted as documented prerequisite, not a blocker)
Accepted by: session — concerns accepted: (1) LogSeverity typo fixed directly in plan text this pass, no further action needed; (2) pytest-installation-required-before-hybrid-gates-run accepted as already-documented EXECUTE prerequisite per plan's own "Test Infra Improvement Notes" section, not treated as blocking CONDITIONAL debt

---

## Autonomous Goal Block

SESSION GOAL: Ship the 3-fix pre-commit worker batch (token-aware chunking, OCR OR-merge,
cross-layer partial-ingest status) plus HIGH#1 noise-filter tightening — all four sections in one
uncommitted worker diff, gated on the same VALIDATE contract.
Charter + umbrella plan: N/A — single plan, no phase program.
Autonomy: standard RIPER-5 gates apply (no standing /goal declared for this session). EXECUTE
requires explicit "ENTER EXECUTE MODE".
Hard stop conditions / safety constraints:
- Recommended EXECUTE order is Fork 3 → Fork 2 → Fork 1 → HIGH#1 (Fork 2 depends on Fork 3's
  partial signal being populated first).
- Do not mark this plan VERIFIED without explicit user confirmation of the two Agent-Probe manual
  smoke checks (UI chip rendering, full-success-path regression) — code-complete is CODE DONE, not
  VERIFIED, per this repo's convention.
- `pytest` must be added to `worker/.venv` before the Hybrid-tier fixtures can run (documented
  prerequisite, not new scope).
- `LogSeverity.Warn` (not `Warning`) — already corrected in plan text at V6; do not reintroduce the
  typo during EXECUTE.
Next phase: EXECUTE: process/general-plans/active/worker-precommit-fixes_06-07-26/worker-precommit-fixes_PLAN_06-07-26.md
Validate contract: inline in plan (## Validate Contract section, this file)
Execute start: `python -m py_compile worker/app/services/chunker.py worker/app/services/loader.py worker/app/api/ingest.py worker/app/schemas/ingest.py worker/app/main.py` (fully-auto) | `dotnet build` (fully-auto) | Agent-Probe: manual UI chip smoke + full-success regression smoke | high-risk pack: no (no high-risk class present)

---

## Next Step

Review this plan carefully. Say **`ENTER VALIDATE MODE`** when ready to proceed to plan validation
(required before implementation — this plan touches a cross-layer contract change, so VALIDATE is
not skippable per the orchestration VALIDATE Gate rules).
