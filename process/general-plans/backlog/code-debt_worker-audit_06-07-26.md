# Code Debt — Worker Audit Findings (06-07-26)

Source: full codebase audit (code-review-graph + vc-code-reviewer), 2026-07-06.
Scope: uncommitted worker diff (8 files, +620/−161) + security/reliability surfaces.
Status: HIGH #1, #2 + LOW #9 + Chunking-quality #16 FIXED via `process/general-plans/active/worker-precommit-fixes_06-07-26/` (all gates green, EVL-confirmed 06-07-26). Rest still parked as debt.

## HIGH (silent data loss — fix before commit)

| # | File:Line | Problem | Fix | Status |
|---|---|---|---|---|
| 1 | `worker/app/services/chunker.py:344-364` | `_SIGNATURE_RE` matches any 2–6-word Titlecase phrase → ordinary Vietnamese headings/proper nouns ("Ủy Ban Nhân Dân") silently deleted as "signatures", no logging | Require positional signal (end-of-doc) or stronger pattern; log filtered chunks at DEBUG | **FIXED** 06-07-26 |
| 2 | `worker/app/services/loader.py:274-318` | `_convert_pdf_batched`: failed page-batch (OOM etc.) silently dropped from merged markdown → doc ingested as "success" with missing pages | Propagate `partial: true` + message in `IngestResponse`; .NET flags document as partially ingested | **FIXED** 06-07-26 |

## MEDIUM

| # | File:Line | Problem | Fix |
|---|---|---|---|
| 3 | `chunker.py:280-299` | Table without `\|---\|` separator returned as ONE unbounded chunk — bypasses chunk_size | Fallback fixed-size line grouping — **FIXED** 06-07-26 as part of Fork 1 (`_pack_rows_by_tokens` handles the no-separator path, EVL-verified non-passthrough) |
| 4 | `loader.py:267-318` | Tables spanning 10-page batch boundary split across independent Docling parses — header/row continuity lost | Page overlap between batches or continuation-merge heuristic |
| 5 | `worker/pyproject.toml` | `pypdfium2` imported (`loader.py:244`) but undeclared — transitive via Docling only | Pin explicitly — **FIXED** 07-07-26 (`pypdfium2>=4` pinned, TOML validated) |
| 6 | `worker/app/api/ingest.py:37-39` | Process-wide `_INGEST_LOCK` serializes ALL ingests | Accepted for laptop; revisit at server deploy (queue-based ingestion) |
| 7 | `worker/app/api/documents.py:184-229` | Worker delete endpoint has no `department_id` filter — trusts .NET caller | Add tenant filter param, pass from `AiWorkerClient` |
| 8 | `worker/app/main.py:61-71` | Qdrant down at boot → worker crashes (Redis path degrades gracefully) | try/except + 503 on affected endpoints |

## LOW

| # | Where | Problem | Status |
|---|---|---|---|
| 9 | `chunker.py:361-363` | Digit-strip in `_is_noise` filters legit reference numbers (`1202/QĐ-BKHCNCGCN`) as noise | **FIXED** 06-07-26 |
| 10 | `chunker.py:37-59` | `_merge_small_chunks`/`_filter_noise_chunks` global across multi-doc list — cross-doc merge risk if caller ever passes >1 doc | |
| 11 | `embedder.py:44-51` | `device="cpu"` hardcoded, no Settings knob | |
| 12 | ingest paths | Two divergent pipelines: `/api/ingest` (new Chunker) vs arq→`DocxProcessor` (old langchain) — same .docx chunks differently; pick canonical, deprecate other | |
| 13 | `RAG_test/` | Untracked, contains REAL govt docs — gitignore `RAG_test/doc/` + `RAG_test/index/` before accidental commit | **FIXED** 07-07-26 (both dirs ignored, `test_pipeline.py` stays trackable — verified via `git check-ignore`) |
| 14 | `src/crawler.py`, `src/eda.py` | Dead one-off scripts — move to `tools/` or delete | |
| 15 | `config.py:50` | Default `gemma2:2b` vs context doc says `qwen2.5:3b` — sync | **FIXED** 07-07-26 (code default → `qwen2.5:3b`, matching live runtime + docs) |
| 20 | `chunker.py:371-395` (`_merge_small_chunks`) | Only merges a tiny chunk INTO the previous one — a tiny chunk that's FIRST in a document (nothing before it) can never merge. Live proof: real ingest produced a standalone chunk containing only `"## Phụ lục 01"` (2 words). Also slips past `_is_noise`'s `PHỤ\s*LỤC\s*\d*` pattern because the leading `"## "` markdown marker breaks the anchored regex match | Merge forward too (into next chunk) when a leading chunk is tiny; strip markdown heading markers before noise-pattern matching |

## Chunking-quality debt (from 8B/low-mid-server assessment, 06-07-26)

| # | Where | Problem | Fix | Status |
|---|---|---|---|---|
| 16 | `chunker.py` (all size checks) vs `config.py:36` | chunk_size measured in WORDS (`len(text.split())`) but `embed_max_length=1024` is TOKENS. Vietnamese ≈1.5–2.5 tokens/word; tables worse (numbers explode). 1024-word table chunk ≈ 2000–3000+ tokens → embedder silently truncates tail — retrieval blind to bottom of big tables. Confirmed live in `RAG_test/index/qdrant_dump.txt` (pre-fix dump): `danh muc VB da duoc ra soat_Signed.pdf` chunk 1 (rows 1-10 of a 12-row table) ≈700-800 words ≈1400-1600 tokens, exceeding the 1024 cap | Token-aware sizing (use bge-m3 tokenizer count) or drop chunk_size to ~400–500 words | **FIXED** 06-07-26 (needs re-ingest of already-embedded docs to take effect) |
| 17 | `chunker.py:125-158` | Sub-chunks from `_recursive_split`/`_group_blocks` lose parent heading breadcrumb — "### Chi tiết" chunk loses "## Dự án XYZ" → embedding misses project-name queries | Prepend heading path (H1>H2>H3) to chunk text or metadata | |
| 18 | `config.py:55` | `retrieval_top_k=12` × up-to-1024-word chunks = worst-case 18k+ token prompts — too heavy for 8B on low-mid server | top_k 6–8 + optional reranker (bge-reranker-v2-m3); cap total context tokens in prompt_builder | **FIXED** 07-07-26 — top_k 12→8 in config.py AND `worker/.env` RETRIEVAL_TOP_K 10→8 (the .env value is the operative override; also rewrote .env BOM-less). Reranker still optional future work |
| 23 | `retriever.py:20` + `rag_system.j2:9` | `SNIPPET_MAX_CHARS=500` truncated every chunk to 500 chars AND the LLM prompt template rendered `doc.snippet` — so the LLM never saw anything past char 500 of any chunk, silently defeating the entire token-cap chunking pipeline. Confirmed live: correct chunk retrieved at rank #1 (score 0.66) but answer sat at char 1648 of 2648 → LLM answered "no information" | `RetrievedSource` gained `text` field (full chunk) used by the prompt template; `snippet` (500 chars) kept for UI citations only; sources SSE payload excludes `text` (wire contract to .NET unchanged) | **FIXED** 07-07-26 (py_compile + pytest 9/9 + template render smoke all pass) |
| 19 | `chunker.py:371-395` | `_merge_small_chunks` appends prose fragments into preceding TABLE chunk; merged chunk can exceed chunk_size and embed cap | Skip merge when previous chunk is table or would exceed cap | **DEFANGED** 06-07-26 — `_enforce_token_cap` final-emission safety net now re-splits any merge artifact that exceeds the cap, so the overflow harm is gone; cosmetic prose-in-table-chunk pollution remains (low) |
| 21 | `loader.py:162` (`_TABLE_RAM_THRESHOLD_GB`) | Below 3GB free RAM, PDF table extraction dropped to bare-converter mode which loses row/cell structure entirely — a real N-row table collapsed into ONE markdown table cell with all rows concatenated as running text. Empirically verified the 3GB gate was overly conservative: forced real TableFormer FAST mode at 2.04GB available — no OOM, ~1.2GB peak delta, correct per-row structure recovered (vs bare mode's blob). Cost is per-10-page-batch (`_PDF_PAGE_BATCH_SIZE`), not scaling with total doc size, so this generalizes beyond the one test PDF | Lowered `_TABLE_RAM_THRESHOLD_GB` 3.0→1.5 (leaves ~300MB margin above observed 1.2GB peak) | **FIXED** 07-07-26 (`py_compile` + `test_loader_partial.py` 2/2 pass; manual re-ingest confirmation of real per-row structure at ~2GB free still recommended) |
| 22 | Docling/torch OCR+layout inference (no single file — process-lifecycle issue, not application code) | Multiprocessing child processes spawned by Docling/torch (OCR/layout inference workers) don't reliably terminate on Windows after their work finishes. Observed 3 times in one session: twice as leftover test-script children (~1.3-2GB each), once from the LIVE production worker process itself (6.5GB, idle, spawned ~11:01 by an earlier operation, never torn down) — this one directly caused a real chat query's SSE stream to break (`HttpIOException: response ended prematurely`) because system RAM was starved to 0.71GB free while it sat there doing nothing. Confirmed idle via 5s CPU-delta=0 check before killing each time — always safe to kill, never disrupted the live worker or any request | Investigate whether Docling/EasyOCR's multiprocessing pool can be run in-process (no subprocess) on this platform, or add explicit pool teardown/join after each conversion; short-term mitigation is manually checking `tasklist \| findstr python.exe` for oversized idle children when things get slow/fail unexpectedly | **MITIGATED** 07-07-26 (4th occurrence broke multi-file upload: 3.1GB corpse → next file's RAM precheck failed at 0.7GB). `ingest.py` now has `_reap_leaked_children()` — terminates idle `multiprocessing.spawn`-signature children in `_run_pipeline`'s `finally` and inside `_check_ram` when RAM is low; `_check_ram` also waits up to 30s for RAM recovery before failing (py_compile + pytest 9/9 + no-op smoke pass). TRUE root cause (why Docling/torch children never exit on Windows) still unfixed — reaper neutralizes the symptom per-ingest |

## Test coverage debt

- Zero automated tests in `worker/`. Priority order: chunker fixtures (headings/tables/noise false-positives) → `_assess_pdf_quality`/`_is_near_empty` → `_convert_pdf_batched` with mocked converter (partial-failure regression) → convert `RAG_test/test_pipeline.py` to real pytest with assertions.
