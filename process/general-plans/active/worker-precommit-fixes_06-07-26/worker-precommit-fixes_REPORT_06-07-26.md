---
phase: worker-precommit-fixes
date: 2026-07-06
status: COMPLETE
feature: general
plan: process/general-plans/active/worker-precommit-fixes_06-07-26/worker-precommit-fixes_PLAN_06-07-26.md
---

# Worker Pre-Commit Fixes — EXECUTE Report

All 4 sections implemented in the LOCKED order (Fork 3 → Fork 2 → Fork 1 → HIGH#1).
All 4 automated gates GREEN. Run was interrupted once by an account session limit after
Fork 2 (step 23); resumed and completed Fork 1 + HIGH#1 + all pytest fixtures. Source edits
from before the cutoff were verified coherent (not redone).

## What Was Done

### Fork 3 — OCR double-pass OR-merge (`worker/app/services/loader.py`)
- `_convert_pdf_batched` return grown `tuple[str, Any]` → `tuple[str, Any, int, list[tuple[int,int]]]`; captures every skipped batch's `(start, end)` into `failed_ranges` (both OOM + generic-error branches). Small-PDF single-pass early return yields `(md, doc, 0, [])`.
- `_load_docling` PDF branch unpacks the 4-tuple from digital pass AND OCR-retry pass; `ocr_failed_ranges` initialised before the `_is_near_empty` branch so it always exists; OR-merge `all_failed_ranges = digital + ocr`; `partial = bool(...)`; Vietnamese `partial_reason` ("Thiếu N nhóm trang: s-e, ...").

### Fork 2 — cross-layer partial-ingest signal (7 files)
- `DoclingResult` (+`partial: bool = False`, `partial_reason: str | None = None`, appended).
- `_run_pipeline` return `int` → new `_PipelineResult` dataclass; aggregates partial-ness across `documents` via `getattr(d, "partial", False)` (robust to mixed `DoclingResult`/`Document` lists); joins reasons.
- `ingest_document` unpacks `_PipelineResult`, passes `partial`/`partial_reason` into `IngestResponse`, logs `partial=%s`.
- `IngestResponse` (+ additive `partial`/`partial_reason` Fields, defaulted).
- `IngestResult.cs` (+ `[JsonPropertyName("partial")] Partial`, `[JsonPropertyName("partial_reason")] PartialReason`).
- `DocumentStatus.cs` (+ `PartiallyIngested = 4`, appended after `Failed = 3`, no renumber).
- `DocumentService.cs`: branch restructured `if (IsSuccess && Partial)` → new `MarkPartiallyIngestedAsync` + `doc.ingest_partial` audit at `LogSeverity.Warn`; `else if (IsSuccess)` → unchanged `MarkReadyAsync`; `else` → unchanged fail path. New helper mirrors `MarkFailedAsync`, reuses `ErrorMessage` column (no migration), truncates at 1000.
- `Documents.cshtml`: StatusChip amber arm "Nhập một phần" before `_ =>`; 5th amber stat card; grid `lg:grid-cols-4` → `lg:grid-cols-5`.
- `Documents.cshtml.cs`: `PartiallyIngestedCount` property + `CountFor(DocumentStatus.PartiallyIngested)` (own bucket, not folded).

### Fork 1 — token-aware chunking (`worker/app/services/chunker.py`, `main.py`, `pyproject.toml`)
- Module `logger` + `AutoTokenizer` import; `Chunker.__init__` gains `token_cap` (default 1024) + chunker-owned `self._tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")`; `_count_tokens` helper.
- `token_counter`/`token_cap` threaded through `_split_markdown_by_headings` → `_recursive_split` → `_group_blocks` → `_split_table_block`. Table splitting uses EXACT token cap (Decision B); heading recursion stays word-based.
- Final-emission safety check `_enforce_token_cap` after `_merge_small_chunks`: re-splits (never truncates, Decision A/C) any over-cap node to a fixpoint, with a guaranteed hard-token-window backstop.
- `main.py` — the one construction site now passes `token_cap=settings.embed_max_length`.
- `pyproject.toml` — explicit `transformers>=4.40.0` pin + `[project.optional-dependencies].test` with `pytest>=8.0.0`.

### HIGH#1 — noise-filter tightening (`worker/app/services/chunker.py`)
- `_TITLE_WORD_RE` added; `_is_noise` signature branch now requires a role/title word (stops deleting ordinary Vietnamese Titlecase proper nouns like "Ủy Ban Nhân Dân").
- DEBUG logging at all three `return True` noise branches.
- Digit-strip bug (debt #9) fixed — `\d` removed from the strip class so "12/QĐ" survives.

## What Was Skipped or Deferred
- Nothing in scope skipped. `.docx`/legacy-`.doc` partial detection remains an explicit Known-Gap (PDF-batching-only mechanism) per plan — not actioned by design.
- Out-of-scope files (`worker/app/config.py`, `queue_worker.py`, `embedder.py`) left intact (pre-existing uncommitted diff, not this batch).

## Test Gate Outcomes
| Gate | Command | Result |
|---|---|---|
| py_compile (5 files) | `worker/.venv/Scripts/python -m py_compile app/services/chunker.py app/services/loader.py app/api/ingest.py app/schemas/ingest.py app/main.py` | GREEN |
| import smoke | `worker/.venv/Scripts/python -c "import app.services.chunker, app.services.loader, app.api.ingest, app.schemas.ingest"` (from worker/) | GREEN |
| pytest | `worker/.venv/Scripts/python -m pytest` (from worker/) | GREEN — 9 passed |
| dotnet build | `dotnet build chatbot.csproj` | GREEN — 0 warn, 0 err |

pytest coverage (9 tests): token-cap-on-oversized-table (1, Hybrid), noise-filter false-positive + true-positive + bare-role + reference-number (4, Fully-Automated), loader failed-ranges + OR-merge (2, Hybrid), ingest partial-propagation + non-partial (2, Hybrid).

## Plan Deviations (within blast radius — documented per §Deviation Handling)
1. **`_split_table_block` refactor** — extracted the row-packing loop into `_pack_rows_by_tokens` so the no-table-header path (used by the final-emission safety check on non-table blocks) actually splits by line instead of returning the whole block as one node. Plan Decision C explicitly anticipated "generic line-boundary splitter, not literal markdown table." Same file/function/intent. Required to make the anti-truncation guarantee real.
2. **`_hard_token_split` backstop added** — plan Decision A's final fallback was "SentenceSplitter," but SentenceSplitter's word-based `chunk_size` cannot honor an arbitrary token cap (proven: token-cap test at cap=40 emitted a 57-token chunk via header+row). Added a token-window slicer that decodes cap-sized id windows — guarantees ≤ cap at any cap, drops nothing. Same file/function, stricter version of the plan's stated anti-truncation intent.
3. **HIGH#1 signature gate** — plan step 25's literal boolean was `_SIGNATURE_RE.match AND _TITLE_WORD_RE.search`, but `_SIGNATURE_RE` (single-line Titlecase shape) can never match the plan's own multi-line true-positive fixture `"KT. GIÁM ĐỐC\nNguyễn Văn A"`, making the specced test unpassable. Implemented the plan's INTENT (short chunk + title word present ⇒ noise) which satisfies BOTH named fixtures. `words <= 6` used (fixtures are 6 words).

All three are within the plan's declared blast radius (chunker.py, same functions, same anti-silent-loss intent) and none touch a hard-stop class (no auth/billing/schema/API/container/secrets).

## Test Infra Gaps Found
- `.NET` side has no xUnit project — `dotnet build` is compile-only; `DocumentService.IngestAsync` branch logic (Ready/PartiallyIngested/Failed) has no automated behavioral coverage. Highest-value future test target. (Pre-existing gap, documented in plan.)
- Two Agent-Probe manual smokes (UI amber chip render on a forced `PartiallyIngested` row; full-success PDF regression stays `Ready`) are NOT automatable and remain unrun — require running worker + .NET + a DB row. These are the only proof for the UI hop and the no-regression criterion.

## Closeout Packet
- **Selected plan:** `process/general-plans/active/worker-precommit-fixes_06-07-26/worker-precommit-fixes_PLAN_06-07-26.md`
- **Finished:** all 26 checklist items across Fork 3/2/1/HIGH#1; 4 new pytest fixture files.
- **Verified:** all 4 automated gates green (py_compile, import smoke, pytest 9/9, dotnet build).
- **Still unverified:** 2 Agent-Probe manual smokes (UI chip + full-success regression) — need running services; plan forbids marking VERIFIED without explicit user confirmation of these.
- **Cleanup remaining:** none blocking. Commit when ready (all four share one uncommitted worker diff boundary).
- **Best next state:** Keep plan in active/ (CODE DONE, not VERIFIED — awaiting the 2 manual smokes). Classification: **Keep in active/testing**.

## Forward Preview
### Test Infra Found
- pytest now installed in `worker/.venv` (9.1.1) + pinned in `pyproject.toml` `[project.optional-dependencies].test`. First worker pytest fixtures established (colocated with source: `app/services/test_*.py`, `app/api/test_*.py`).
- bge-m3 tokenizer config now cached in HF cache (`~/.cache/huggingface/hub/models--BAAI--bge-m3`) — Hybrid token-cap test runs offline after this.

### Blast Radius Changes
- `DocumentStatus` now has a 5th value `PartiallyIngested = 4`. Any FUTURE exhaustive switch on `DocumentStatus` must handle it. Confirmed no other exhaustive switch exists today (only `StatusChip`, which has a `_ =>` default arm).
- `Chunker.__init__` signature grew by `token_cap` (defaulted — back-compatible). `_convert_pdf_batched` return grew to 4-tuple (2 internal callers, both updated).

### Commands to Stay Green
- `cd worker && ./.venv/Scripts/python -m pytest`
- `cd worker && ./.venv/Scripts/python -m py_compile app/services/chunker.py app/services/loader.py app/api/ingest.py app/schemas/ingest.py app/main.py`
- `dotnet build chatbot.csproj`

### Dependency Changes
- `transformers>=4.40.0` explicit pin added (was transitive; v5.10.1 present).
- `pytest>=8.0.0` added to worker test extras.
