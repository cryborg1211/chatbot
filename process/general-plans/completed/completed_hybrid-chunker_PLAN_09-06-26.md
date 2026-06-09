# Hybrid A+C Chunker — Implementation Plan

**Date:** 09-06-26
**Complexity:** Simple
**Status:** ✅ VERIFIED

## Overview

Replace the current `SentenceSplitter`-only chunker in `worker/app/services/chunker.py` with a
hybrid strategy: Docling's `HierarchicalChunker` as the primary splitter (Approach A), followed
by a custom Markdown-aware post-processing pass (Approach C) that repairs oversized chunks by
splitting at table-row boundaries while re-attaching column headers to every continuation chunk.
The ingest pipeline interface (`ingest.py`, `main.py`) is unchanged. Existing Qdrant data for
all documents must be wiped and re-ingested after the fix lands.

---

## Quick Links

- [Goals and Success Metrics](#goals-and-success-metrics)
- [Phase Completion Rules](#phase-completion-rules)
- [Execution Brief](#execution-brief)
- [Scope](#scope)
- [Assumptions and Constraints](#assumptions-and-constraints)
- [Functional Requirements](#functional-requirements)
- [Acceptance Criteria](#acceptance-criteria)
- [Implementation Checklist](#implementation-checklist)
- [Touchpoints](#touchpoints)
- [Public Contracts](#public-contracts)
- [Blast Radius](#blast-radius)
- [Verification Evidence](#verification-evidence)
- [Risks and Mitigations](#risks-and-mitigations)
- [Integration Notes](#integration-notes)
- [Resume and Execution Handoff](#resume-and-execution-handoff)
- [Cursor + RIPER-5 Guidance](#cursor--riper-5-guidance)

---

## Goals and Success Metrics

**Goals:**

- Table rows are never split from their column headers in any chunk.
- Monster cells (3000+ chars) are handled without producing chunks that exceed bge-m3's 8192-token
  soft cap.
- Plain-text and legacy `.doc` ingest paths keep working without modification.
- The change is self-contained: no API surface changes, no new env vars required, no schema
  migration.

**Success Metrics:**

- Zero chunks where a `|---|` separator row or data row appears without its header row in the same
  chunk (verified manually on at least 3 files from `data/lamdong_docs/`).
- Chunk count per document changes (expected: fewer, larger chunks for prose; more, smaller chunks
  for tables).
- `/api/ingest` returns HTTP 200 with non-zero `chunk_count` for every test document.
- `/api/query` returns sourced answers that cite correct table cells from re-ingested documents.

---

## Phase Completion Rules

A phase is NOT complete until:

1. **Integration Test** — Works with other system pieces end-to-end.
2. **Manual Test** — User can trigger the action and see the correct result.
3. **Data Verification** — Qdrant payload confirmed correct structure.
4. **Error Handling** — Failure cases handled gracefully.
5. **User Confirmation** — User says "it works."

Status meanings:

- ⏳ PLANNED — Not started
- 🔨 CODE DONE — Written but not E2E tested
- 🧪 TESTING — Currently being tested
- ✅ VERIFIED — Tested AND confirmed working
- 🚧 BLOCKED — Has issues

---

## Execution Brief

### Phase 1 — Loader returns DoclingDocument alongside Markdown (Step 1)

**What happens:** `_load_docling` in `loader.py` is updated to return `result.document`
(the native `DoclingDocument` object) alongside the Markdown string, packaged inside a thin
`DoclingResult` dataclass. The public `load_documents()` signature stays `-> list[Any]` so
`ingest.py` requires no change at this step.

**Test:** Manually call `load_documents(pdf_bytes, "application/pdf", "test.pdf")` in a Python
REPL; assert the returned object carries both `.text` (Markdown string) and `.docling_doc`
(DoclingDocument instance).

**Verify:** `type(result[0].docling_doc).__name__ == "DoclingDocument"`.

**Done when:** No exception; both fields populated for all supported MIME types.

---

### Phase 2 — HierarchicalChunker primary split (Steps 2-4)

**What happens:** `Chunker.__init__` instantiates `HierarchicalChunker(max_tokens=chunk_size)`.
`Chunker.split()` accepts `list[Any]` (list of `DoclingResult` for Docling docs, list of plain
LlamaIndex `Document` for text), branches on type, and returns `list[TextNode]` as before.
For plain-text documents the existing `SentenceSplitter` path remains intact.

**Test:** Ingest one `.docx` file from `data/lamdong_docs/` via the live HTTP endpoint:
```
curl -X POST http://localhost:8001/api/ingest \
  -H "X-Worker-Api-Key: <key>" \
  -F "file=@data/lamdong_docs/Dinh muc KTKT Thiet lap duy tri bao quan chuan do luong.docx" \
  -F "document_id=<uuid>" \
  -F "department_id=IT" \
  -F "original_name=test.docx" \
  -F "mime_type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

**Verify:** HTTP 200, `chunk_count > 0`; retrieve a random chunk from Qdrant and confirm it
contains a complete table row (header + at least one data row) without cut separators.

**Done when:** At least 3 `.docx`/`.pdf` files ingest without error; no lone `|---|` lines in
any stored chunk.

---

### Phase 3 — Markdown-aware oversized-chunk post-processing (Steps 5-7)

**What happens:** After `HierarchicalChunker` produces chunks, a `_split_oversized_chunks()`
function iterates over each chunk. Chunks whose token count exceeds `chunk_size` are passed to
`_split_markdown_chunk()`. That function:

1. Detects table blocks (contiguous lines starting with `|`).
2. Identifies the header row (first `|` line) and separator row (second `|` line with `---`).
3. Splits at row boundaries, prepending the header + separator to every continuation chunk.
4. For non-table (prose) sections exceeding the limit, delegates to `SentenceSplitter`.

**Test:** Find or construct a chunk with a 3000-char cell. Confirm the post-processor splits it
into multiple chunks, each beginning with the column header row.

**Verify:** No chunk in Qdrant exceeds `len(text) > chunk_size * 6` (rough token-to-char
heuristic). All table chunks start with a `|` header line.

**Done when:** Monster-cell documents (e.g., `Dinh muc KTKT Thiet lap duy tri bao quan chuan do
luong.docx`) produce query answers that correctly cite specific rows.

---

### Phase 4 — Re-ingestion + smoke test (Step 8)

**What happens:** Delete all existing Qdrant vectors for affected documents via the
`/api/documents/delete` endpoint (or `vector_store.delete_document()`), then re-ingest all
documents through the updated pipeline.

**Test:** Issue a query via `/api/query` that should match a specific table value in one of the
re-ingested documents. Confirm the returned answer cites the correct row.

**Verify:** Qdrant `count` endpoint shows expected number of points; no points have empty `text`
payload.

**Done when:** At least one table-lookup query returns a correct answer confirmed by the user.

---

### Expected Outcome

- `chunker.py` contains `HierarchicalChunker` as primary + Markdown-aware fallback.
- `loader.py` exposes `DoclingResult` with both `.text` and `.docling_doc`.
- All existing ingest, query, and plain-text paths work unchanged.
- Qdrant collection holds re-ingested data with structurally intact table chunks.
- No regressions on `.txt` and `.doc` paths.

---

## Scope

**In-Scope:**

- `worker/app/services/chunker.py` — full rewrite.
- `worker/app/services/loader.py` — `_load_docling` return type, new `DoclingResult` dataclass.
- `worker/app/api/ingest.py` — adapting the `raw_texts` extraction call if the node API changes
  (expected: no change since `TextNode.get_content()` is preserved).
- `worker/pyproject.toml` — no new deps needed (`docling` is already present).
- Re-ingestion of all existing documents (manual operational step, not a code step).

**Out-of-Scope:**

- `config.py` — no new env vars.
- `vectorstore.py` — interface unchanged.
- `chunk_metadata.py` — unchanged.
- `main.py` — `Chunker(chunk_size=..., chunk_overlap=...)` constructor call unchanged.
- C# `.NET` backend — no changes.
- Any change to the Qdrant collection schema or payload keys.
- Automated test suite (project has none; manual verification is the test protocol).

---

## Assumptions and Constraints

- `docling>=2.0.0` is already installed (confirmed in `pyproject.toml`).
- `HierarchicalChunker` is importable from `docling.chunking`. If the import fails on the
  installed version, the executor must fall back to `docling.chunking.hierarchical_chunker`.
- `result.document` from `DocumentConverter.convert()` is a `DoclingDocument` instance.
- `HierarchicalChunker` accepts `max_tokens` as a constructor parameter (must be verified at
  execution time; if the param name differs, adjust).
- `HierarchicalChunker.chunk(doc)` returns an iterable of objects with a `.text` attribute.
- Token counting for the oversized-chunk check uses `len(text.split())` as an approximation
  (not a true BPE count); this is conservative and safe.
- The plain-text path (`text/plain`) continues to use `SentenceSplitter` exactly as today.
- Legacy `.doc` path converts to `.docx` via LibreOffice, then goes through `_load_docling`, so
  it automatically gains the new Docling chunking path.
- `chunk_overlap` is not directly supported by `HierarchicalChunker`; it is used only by the
  `SentenceSplitter` fallback for oversized prose chunks.

---

## Functional Requirements

1. `Chunker.split(documents)` accepts `list[Any]` and returns `list[Any]` (list of `TextNode`).
   Signature unchanged.
2. When input documents contain `DoclingResult` objects, `HierarchicalChunker` is used.
3. When input documents are plain LlamaIndex `Document` objects (text/plain path), `SentenceSplitter` is used.
4. Chunks produced by `HierarchicalChunker` that exceed `chunk_size` tokens are post-processed by
   `_split_markdown_chunk`.
5. `_split_markdown_chunk` keeps the table header + separator row attached to every split
   continuation of a table block.
6. `_split_markdown_chunk` delegates oversized prose sections to `SentenceSplitter(chunk_size,
   chunk_overlap)`.
7. `DoclingResult` is a dataclass with fields `text: str`, `docling_doc: Any`,
   `metadata: dict[str, Any]`.
8. `load_documents()` returns `list[DoclingResult]` for Docling-parsed paths and `list[Document]`
   for the plain-text path. The return type annotation stays `list[Any]`.
9. `ingest.py` extracts raw texts via `n.get_content()` — this must keep working for all
   `TextNode` outputs from the new `Chunker`.

---

## Acceptance Criteria

1. `chunker.split([doc])` on a `.docx` with a multi-row table returns at least one chunk whose
   text starts with a `|` header line followed by a `|---|` separator line and at least one data
   row.
2. No chunk in the output has a `|---|` line as its first line (separator orphaned from header).
3. `chunker.split([doc])` on a plain-text `Document` returns the same output as the original
   `SentenceSplitter`-based implementation (same approximate chunk count, no regressions).
4. `load_documents(pdf_bytes, "application/pdf", "x.pdf")` returns a list whose first element has
   a non-empty `.text` attribute and a non-`None` `.docling_doc` attribute.
5. `load_documents(txt_bytes, "text/plain", "x.txt")` still returns a plain `Document` object
   (not a `DoclingResult`).
6. `POST /api/ingest` returns HTTP 200 with `chunk_count > 0` for `.pdf`, `.docx`, `.doc`, and
   `.txt` test files.
7. `POST /api/query` for a query matching a table cell in a re-ingested document returns an answer
   that correctly identifies the row value (no hallucination of adjacent rows).
8. Worker starts without import errors after the change: `uvicorn app.main:app --port 8001`.

---

## Implementation Checklist

1. **Add `DoclingResult` dataclass to `loader.py`** — define `DoclingResult(text: str,
   docling_doc: Any, metadata: dict[str, Any])` at module level just below the imports. Use
   `@dataclasses.dataclass` (stdlib, no new dep).

2. **Update `_load_docling` in `loader.py`** — after `markdown_text = result.document.export_to_markdown()`,
   capture `docling_doc = result.document`. Return
   `[DoclingResult(text=markdown_text, docling_doc=docling_doc, metadata={"source": original_name, "parser": "docling"})]`
   instead of the current `[Document(...)]`.

3. **Verify `_load_text` unchanged** — confirm `_load_text` still returns `[Document(...)]` (no
   change needed; this is a read step, not an edit step — confirm before moving on).

4. **Rewrite `Chunker.__init__` in `chunker.py`** — import and instantiate `HierarchicalChunker`
   and `SentenceSplitter` side-by-side:
   - `from docling.chunking import HierarchicalChunker`
   - `self._hier = HierarchicalChunker(max_tokens=chunk_size)`
   - `from llama_index.core.node_parser import SentenceSplitter`
   - `self._fallback = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)`
   - Store `self._chunk_size = chunk_size` and `self._chunk_overlap = chunk_overlap`.

5. **Rewrite `Chunker.split` in `chunker.py`** — branch on document type:
   - If element is a `DoclingResult` (check `hasattr(doc, "docling_doc")`): call
     `self._hier.chunk(doc.docling_doc)`, collect `.text` from each chunk into a list, wrap each
     as `TextNode(text=chunk_text, metadata=doc.metadata)`.
   - If element is a plain `Document` (LlamaIndex): pass to `self._fallback.get_nodes_from_documents([doc])`.
   - Collect all `TextNode` objects into one flat list.
   - Pass the full list through `_split_oversized_chunks(nodes, self._chunk_size, self._fallback)`.
   - Return the resulting flat list.

6. **Add `_split_oversized_chunks` function to `chunker.py`** — iterate over nodes; for any
   `TextNode` whose `len(node.get_content().split()) > chunk_size`, call
   `_split_markdown_chunk(node.get_content(), chunk_size, fallback_splitter)` and replace that
   node with the returned list. Return the new flat list of nodes.

7. **Add `_split_markdown_chunk` function to `chunker.py`** — algorithm:
   a. Split input text into lines.
   b. Walk lines; when a contiguous block of `|`-prefixed lines is detected, treat it as a table
      block.
   c. Within a table block: first non-empty `|` line = header row; second `|` line matching
      `/^\|[\s\-|]+\|$/` = separator row.
   d. Group data rows into sub-chunks not exceeding `chunk_size` tokens. Each sub-chunk is:
      `header_row + "\n" + separator_row + "\n" + data_rows`.
   e. Non-table text between or around table blocks: if length exceeds `chunk_size` tokens, pass
      to `fallback_splitter.get_nodes_from_documents([Document(text=prose)])` and collect texts.
      Otherwise keep as a single chunk.
   f. Return `list[TextNode]` with metadata `{}` (caller handles metadata injection if needed).

8. **Confirm `ingest.py` unchanged** — `raw_texts = [n.get_content() for n in nodes]` works for
   `TextNode` regardless of how it was constructed. Read step only; do not edit.

9. **Smoke-test worker startup** — start uvicorn on port 8001; confirm no import errors in the
   first 5 lines of log.

10. **Ingest test: `.docx` with tables** — POST `Dinh muc KTKT Thiet lap duy tri bao quan chuan
    do luong.docx` to `/api/ingest`; confirm HTTP 200 and `chunk_count > 0`; spot-check one Qdrant
    point via `GET http://localhost:6333/collections/ld3_knowledge/points/<point_id>` and confirm
    `text` field starts with a full table header.

11. **Ingest test: `.txt` regression** — POST any `.txt` file; confirm HTTP 200 and output chunks
    match the old SentenceSplitter behaviour (no table logic applied).

12. **Ingest test: legacy `.doc`** — POST one legacy `.doc` file from `data/lamdong_docs/`; confirm
    HTTP 200 (LibreOffice → Docling → HierarchicalChunker path works end-to-end).

13. **Delete stale Qdrant data and re-ingest all documents** — for each document that was previously
    ingested, call `DELETE /api/documents/<document_id>` via the .NET API (or call
    `vector_store.delete_document(document_id)` directly from the Python side); then trigger
    re-ingestion through the .NET admin UI or via direct POST to `/api/ingest`.

14. **Query smoke test** — issue a natural-language query that should match a table row (e.g., a
    budget figure or equipment spec) via the .NET chat UI or direct POST to `/api/query`; confirm
    the answer cites the correct value and does not hallucinate adjacent rows.

---

## Touchpoints

| Layer | File | Nature of change |
|---|---|---|
| Python worker — loader | `worker/app/services/loader.py` | Adds `DoclingResult` dataclass; changes return type of `_load_docling` |
| Python worker — chunker | `worker/app/services/chunker.py` | Full rewrite; adds `HierarchicalChunker`, `_split_oversized_chunks`, `_split_markdown_chunk` |
| Python worker — ingest API | `worker/app/api/ingest.py` | Read-only verification; no edit expected |
| Python worker — main lifespan | `worker/app/main.py` | Read-only verification; `Chunker(chunk_size, chunk_overlap)` call unchanged |
| Qdrant data | `ld3_knowledge` collection | Requires full delete + re-ingest of existing documents |

---

## Public Contracts

**Unchanged public contracts (must not break):**

1. `load_documents(file_bytes, mime_type, original_name) -> list[Any]` — signature unchanged; callers receive objects with `.text`-like access via `n.get_content()` downstream.
2. `Chunker(chunk_size: int, chunk_overlap: int)` — constructor signature unchanged.
3. `Chunker.split(documents: list[Any]) -> list[Any]` — signature unchanged; returns `list[TextNode]`.
4. `TextNode.get_content()` — used in `ingest.py` line 115; must keep working.
5. `VectorStore.upsert_chunks(document_id, department_id, original_name, chunks: list[str], vectors: list[list[float]])` — unchanged.
6. `/api/ingest` HTTP request/response shape — unchanged.

**New internal types (not exposed to callers):**

- `DoclingResult` dataclass in `loader.py` — internal to the loader/chunker boundary only.
- `_split_oversized_chunks` and `_split_markdown_chunk` — module-private functions in `chunker.py`.

---

## Blast Radius

**Affected at runtime:**

- Every document ingest operation that goes through `_load_docling` (PDF, DOCX, legacy DOC paths).
- The Qdrant `ld3_knowledge` collection must be considered dirty until all documents are re-ingested.

**Not affected at runtime:**

- `.txt` ingest path (still uses `SentenceSplitter`).
- Query path (`/api/query`, `retriever.py`, `llm_router.py`, `prompt_builder.py`) — no changes.
- C# backend, SignalR hub, identity, admin pages — no changes.
- Qdrant schema (payload keys, vector size, collection name) — no changes.
- `chunk_metadata.py` (`prepend_document_context_to_chunks`) — no changes.

**Rollback plan:** If the new chunker causes regressions, revert `chunker.py` to the original
`SentenceSplitter`-only implementation and revert `loader.py` to returning plain `Document`
objects. Both files are small and the diff is straightforward. Re-ingest all documents again after
rollback. The original implementations are preserved in git history at HEAD (`4a5e881`).

---

## Verification Evidence

The following evidence must be collected before marking this plan ✅ VERIFIED:

1. **Import proof** — worker log at startup shows no `ImportError` for `docling.chunking`.
2. **Ingest success** — HTTP 200 + `chunk_count > 0` for at least one `.docx`, one `.pdf`, one
   `.doc`, and one `.txt` file.
3. **Table integrity proof** — spot-check of at least 3 Qdrant points from a table-heavy document
   confirms each chunk starts with a full header row.
4. **No orphan separator rows** — `grep` over exported chunk texts shows no chunk whose first line
   matches `/^\|[\s\-|]+\|$/`.
5. **Query accuracy** — at least one table-lookup query returns the correct row value via
   `/api/query` or the chat UI.
6. **Plain-text regression** — a `.txt` file produces the same approximate chunk count as before
   (within ±20%).

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `HierarchicalChunker` import path differs between Docling versions | Medium | Verify import at execution time; fallback to `from docling.chunking.hierarchical_chunker import HierarchicalChunker` |
| `HierarchicalChunker.chunk(doc)` returns an object without `.text` attribute | Medium | At execution time, inspect the `Chunk` object fields; use `chunk.text` or `str(chunk)` as needed |
| `max_tokens` param name differs | Low | Check `HierarchicalChunker` constructor signature with `inspect.signature`; adjust if needed |
| Vietnamese govt docs with zero `#` headers produce degenerate chunk trees | Medium | Covered by Approach C fallback: table block detection does not depend on heading structure |
| Monster cell (3000+ chars) still exceeds chunk after table-row splitting | Low | `_split_markdown_chunk` delegates very long individual cells to `SentenceSplitter` prose path |
| Re-ingestion window leaves Qdrant in partial state | Medium | Re-ingest is idempotent (Qdrant point IDs are UUID5 deterministic); partial runs are safe to restart |
| `DoclingResult` not picklable (arq Redis queue serialization) | Low | `queue_worker.py` serializes only job parameters (file bytes + strings), not `DoclingResult` objects — no impact |

---

## Integration Notes

- **Docling version:** `docling>=2.0.0` is pinned in `pyproject.toml`. `HierarchicalChunker` was
  introduced in Docling 2.x. No version bump needed.
- **LlamaIndex `TextNode`:** Wrapping `HierarchicalChunker` output in `TextNode(text=...,
  metadata=...)` is the correct bridge; `TextNode` is already imported transitively via
  `llama-index-core`.
- **arq queue worker:** `queue_worker.py` receives job parameters as plain strings/bytes and calls
  `load_documents` + `chunker.split` itself. Since both public interfaces are unchanged, no
  modification is needed.
- **Re-ingestion tooling:** The .NET `DocumentIngestionWorker` background service can be used to
  re-ingest by resetting document statuses to `Pending` in the SQL database. Alternatively, call
  the Python `/api/ingest` endpoint directly for each file.
- **No migration of Qdrant schema:** The payload shape (`document_id`, `department_id`,
  `chunk_index`, `text`, `original_name`) is unchanged; new chunks will simply have better
  `text` content.

---

## Resume and Execution Handoff

**If this plan is resumed in a new session:**

1. Read `process/context/all-context.md` to re-establish project context.
2. Read this plan file from top to bottom.
3. Check which checklist items are ticked; start from the first unticked item.
4. Key files to re-read before implementing:
   - `worker/app/services/chunker.py` (current state)
   - `worker/app/services/loader.py` (current state)
   - `worker/app/api/ingest.py` (pipeline wiring; read-only)
5. If Qdrant already contains new-style chunks (Step 13 was partial), run a point spot-check
   before triggering a full delete+re-ingest.

**State machine:**

```
Steps 1-2  → loader.py edited
Steps 3    → loader.py verified (read only)
Steps 4-7  → chunker.py fully rewritten
Step 8     → ingest.py verified (read only)
Step 9     → worker boots clean
Steps 10-12→ per-format smoke tests pass
Step 13    → Qdrant re-ingested
Step 14    → query accuracy confirmed → PLAN COMPLETE
```

**Critical invariant:** `ingest.py` line 115 `raw_texts = [n.get_content() for n in nodes]` must
work at every intermediate state. If `Chunker.split` ever returns something that doesn't support
`get_content()`, the ingest endpoint will throw a 500 for all documents.

---

## Cursor + RIPER-5 Guidance

**Cursor Plan mode:**

- Import the Implementation Checklist steps 1-14 directly.
- Execute all steps in one session; no approval gates between consecutive steps.
- After Step 9 (startup smoke test), pause and confirm no import errors before proceeding.
- After Step 12 (all format tests), pause and confirm before triggering re-ingestion (Step 13).

**RIPER-5 mode:**

- RESEARCH: ✅ Complete — pipeline, loader, chunker, config, and Qdrant contracts reviewed.
- INNOVATE: ✅ Complete — Hybrid A+C chosen; alternatives (pure SentenceSplitter, LangChain
  RecursiveCharacterTextSplitter, pure Markdown regex splitter) rejected.
- PLAN: ✅ Complete — this document.
- EXECUTE: ✅ Complete — steps 1-14 implemented; HierarchicalChunker + Markdown-aware
  post-processor deployed; all format smoke tests passed; Qdrant re-ingested.
- VERIFY: ✅ Complete — Verification Evidence items 1-6 confirmed. Plan archived to completed/.

**This plan is COMPLETE. Archived to `process/general-plans/completed/`.**
