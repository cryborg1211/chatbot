# Session Handoff — 2026-07-15

Branch: `main` (synced with origin — Step 0 pushed the 6 prior commits)
Last commit: `eddd32e` — "chore: audit artifacts, debt ledger, gitignore for real govt test docs"
**All Phase 0 + Phase 1 work below is UNCOMMITTED** (17 files: 10 modified, 7 new).

This session implemented **Phase 0 (Ingestion Integrity)** + **Phase 1 (Vietnamese Hybrid Retrieval)** from the RAG architecture audit. The audit itself is in chat history — verify any claim against code before acting.

---

## TL;DR — Where Things Stand

| Item | Status |
|---|---|
| Step 0: push 6 prior commits to origin | ✅ DONE (synced) |
| Phase 0: per-page density classifier + manifest | ✅ CODE-COMPLETE, tested |
| Phase 1: segmenter + schema v2 + hybrid RRF + reranker + HyDE + config | ✅ CODE-COMPLETE, tested |
| Unit tests | ✅ 27/27 pass |
| **Live re-ingest + eval measurement** | ⛔ **BLOCKED — Qdrant not running** |
| Commit | ⛔ Not committed yet |

**The single most important thing to know:** all code is written and unit-tested, but it has **NOT been validated end-to-end against a live Qdrant**. The collection migration + re-ingest + eval measurement is the one open gate. Do not assume the hybrid retrieval "works" until you run the eval and see the numbers.

---

## What Was Done (All Uncommitted)

### Step 0 — Push committed work
- `git push origin main` shipped the 6 commits that were ahead of origin. No code change. `main` now in sync.

### Phase 0 — Stop Silent Data Loss on Hybrid PDFs

**Root cause fixed:** `loader.py` `_is_near_empty()` measured WHOLE-DOCUMENT words/page. A 50-page PDF with 5 scanned annex pages passed the aggregate check → scanned pages silently never OCR'd.

**Fix — preemptive per-page density routing (`worker/app/services/loader.py`):**
- New `_classify_page_density(text)` → `"text"` / `"scan"` using chars + alpha-ratio thresholds (mirrors `_assess_pdf_quality` heuristics, per-page not per-doc)
- New `_probe_page_density(file_path, total_pages)` — reads the native text layer via **pypdfium2** (already a dep) BEFORE any Docling parse. This is the architecturally-correct "route before parse" — no wasted OCR on text pages.
- New `_group_density_runs(densities)` — compresses per-page labels into contiguous same-class runs, preserving document order
- New `_convert_pdf_page_ranges()` — converts ONLY specific page ranges (for OCR-targeted batches), sibling to `_convert_pdf_batched`
- Rewrote the PDF branch in `_load_docling`: probe density → group into ordered runs → text runs use digital converter, scan runs use OCR converter → merge. Old whole-doc OCR fallback kept as a safety net (preserves the existing OR-merge test).

**Ingestion manifest (`PageRoute`):**
- `DoclingResult.page_routes: list[dict]` — one entry per page: `{page, density, chars, ocr_used, dropped}`
- `IngestResponse.page_routes: list[PageRoute]` (`worker/app/schemas/ingest.py`) — new Pydantic model, additive/defaulted so old .NET clients keep working
- `_PipelineResult.page_routes` + aggregated in `ingest.py` `_run_pipeline`, surfaced in the success response + structured log line (`pages=%d scanned=%d dropped=%d`)

### Phase 1 — Vietnamese Hybrid Retrieval (server-side on Qdrant 1.10+)

**Architecture:** leverages Qdrant native full-text BM25 + `query_points(prefetch=[dense, fulltext], fusion=RRF)` — NO separate BM25 library. The critical invariant is **identical segmentation at ingest and query**, centralized in one module.

**1.1 Vietnamese segmenter (`worker/app/services/segmenter.py` — NEW):**
- `pyvi.ViTokenizer` (CRF-based, ~MB). `segment(text)` → `"khách hàng"` becomes `"khách_hàng"` so Qdrant's whitespace tokenizer treats compound words as one token.
- Lazy singleton, idempotent, fails safe (returns raw text on any error — never breaks the pipeline).
- `pyvi>=0.1.0` added to `worker/pyproject.toml`.

**1.2 Collection schema v2 (`worker/app/services/vectorstore.py`):**
- Named vector: `"dense"` (1024, cosine) instead of the old unnamed single vector
- Payload TEXT index on `text_segmented` (for native Qdrant BM25)
- Payload now: `{document_id, department_id, chunk_index, text (raw, for LLM), text_segmented (indexed), original_name}`
- `upsert_chunks()` segments each chunk via `segment()` and stores both fields. Accepts optional pre-segmented texts.
- **⚠ Migration required:** old collection has unnamed vector + no text index. Must delete + recreate via `migrate_collection.py`, then re-ingest.

**1.3 Hybrid retriever (`worker/app/services/retriever.py` — rewritten):**
- `Retriever.search()` takes `query_vector` (dense) + `query_text_segmented` (BM25) + `department_id`
- Builds TWO prefetches: dense (using="dense") + BM25 fulltext (using="" = text index), each with `limit=prefetch_limit`
- **TENANT FILTER applied to BOTH prefetches** via shared `_tenant_filter()` — this is the security-critical invariant, unit-tested
- Server-side RRF fusion: `query=FusionQuery(fusion=Fusion.RRF)`, k tunable via `Settings.hybrid_rrf_k` (default 60)

**1.4 Reranker tier (`worker/app/services/reranker.py` — NEW):**
- `bge-reranker-v2-m3` via `sentence-transformers.CrossEncoder` (multilingual/Vietnamese-strong)
- **Lazy-loaded + RAM-guarded:** loads on first `rerank()` only if `available_ram ≥ reranker_ram_threshold_gb` (default 3.0GB); else skips with warning + returns input order
- Configurable on/off via `Settings.reranker_enabled`
- Flow: RRF top-N → cross-encoder rescore (query, chunk_text) → return top-k

**1.5 HyDE (`worker/app/services/hyde.py` — NEW):**
- `generate_hypothetical_answer(query, llm)` — lightweight LLM (existing Ollama `qwen2.5:3b` via `LlmRouter`) generates 2–3 sentence hypothetical Vietnamese answer
- **Gated:** default OFF (`Settings.hyde_enabled=False`) until eval validates a precision gain. Only active for queries < `hyde_max_query_chars` (default 80).
- Latency guard: fails safe to raw query embed. HyDE replaces dense vector ONLY; BM25 branch still uses original segmented query (keyword match on actual user words is correct).

**1.6 Config surface (`worker/app/config.py`):**
- 7 new Settings knobs: `hybrid_rrf_k` (60), `prefetch_top_n` (50), `reranker_enabled` (True), `reranker_model`, `reranker_ram_threshold_gb` (3.0), `hyde_enabled` (False), `hyde_max_query_chars` (80)

**1.7 Wiring (`worker/app/api/query.py` + `main.py`):**
- `query.py` `_stream_events`: (optional HyDE) → segment query → hybrid retrieve → (optional rerank) → build messages → stream LLM. HyDE + reranker both gated + fail-safe.
- `main.py` lifespan: instantiates `Retriever(rrf_k=...)`, `Reranker(...)` (None if disabled), stashes `settings` on `app.state`. Worker version bumped to `0.2.0`.

### Phase 1.7 — Eval harness (`RAG_test/eval_retrieval.py` — NEW)
- Computes **recall@k, MRR, nDCG** for 3 configs: dense-only (baseline) vs hybrid (RRF) vs hybrid+reranker
- 7 labeled Vietnamese queries referencing the 4 real docs in `RAG_test/doc/`
- Metric implementations unit-tested (recall/RR/nDCG math verified)
- `_build_dense_only_retriever()` baseline bypasses BM25 for fair comparison

### Phase 1.8 — Tests (27/27 pass)
- `test_segmenter.py` (7 tests) — compound-word segmentation contract, no content dropped
- `test_retriever_fusion.py` (8 tests) — **tenant filter on BOTH prefetches**, dense uses named vector, BM25 uses text index, RRF fusion used, missing dept raises
- `test_loader_partial.py` (7 tests) — 5 new Phase 0 density/run-grouper tests + 2 adapted legacy tests

### Migration script (`RAG_test/migrate_collection.py` — NEW)
- One-command re-create + re-ingest: deletes old collection, creates v2 schema, re-ingests all 4 docs, prints Phase 0 manifest per doc
- Confirms before deleting; pass `--yes` to skip prompt

---

## BLOCKER — Do This First

### Re-ingest + Live Eval (CRITICAL — the one unverified gate)

Qdrant was NOT running during this session (`WinError 10061 connection refused`). All code is unit-tested but the hybrid retrieval has never run against a real collection. **Do not trust it until measured.**

```bash
# 1. Start Qdrant 1.10+ (must support named vectors + text index + RRF)
#    e.g. docker run -p 6333:6333 qdrant/qdrant

# 2. Re-create collection with v2 schema + re-ingest the 4-doc corpus
cd worker
python -m RAG_test.migrate_collection --yes
# Downloads bge-m3 (~2GB) on first run. Prints per-doc page manifest.
# Watch the "dropped" count — Phase 0 should show 0 dropped for the text docs.

# 3. Run the eval
python -m RAG_test.eval_retrieval EVAL 5
# Prints recall@5 / MRR / nDCG for dense vs hybrid vs hybrid+reranker
```

**Decision gate after eval:** the plan says config flips (reranker default-ON, HyDE ON) only advance on a POSITIVE delta. If reranker shows no gain or tanks recall, set `reranker_enabled=False` and document why. If HyDE helps, flip `hyde_enabled=True`.

**Memory watch:** bge-m3 (~2GB) + bge-reranker-v2-m3 (~2GB) on the 1.5–4GB-free host is tight. The reranker is RAM-guarded (skips below 3GB free with a warning). If eval shows reranker OOMs, default it OFF and note it.

---

## Commit

Everything is uncommitted (17 files). Suggested split:

```bash
# Phase 0: ingestion integrity
git add worker/app/services/loader.py worker/app/services/test_loader_partial.py \
        worker/app/api/ingest.py worker/app/schemas/ingest.py
git commit -m "feat(worker): per-page text-density routing + ingestion manifest (Phase 0)

Replaces whole-doc reactive OCR fallback with preemptive per-page density
classification (pypdfium2 text-layer probe). Hybrid PDFs (text + scanned
annex pages) now route scan pages to OCR individually instead of silently
dropping them. Per-page manifest surfaced in IngestResponse."

# Phase 1: hybrid retrieval
git add worker/app/services/segmenter.py worker/app/services/reranker.py \
        worker/app/services/hyde.py worker/app/services/retriever.py \
        worker/app/services/vectorstore.py worker/app/api/query.py \
        worker/app/config.py worker/app/main.py worker/pyproject.toml
git commit -m "feat(worker): Vietnamese hybrid retrieval — BM25+RRF+reranker+HyDE (Phase 1)

Server-side RRF fusion over dense (bge-m3) + BM25 (Qdrant native full-text
on pyvi-segmented text). Cross-encoder reranker (bge-reranker-v2-m3,
lazy+RAM-guarded). HyDE gated off pending eval. Collection schema v2
(named dense vector + text_segmented index)."

# Phase 1: tests + eval harness
git add worker/app/services/test_segmenter.py worker/app/services/test_retriever_fusion.py \
        RAG_test/eval_retrieval.py RAG_test/migrate_collection.py
git commit -m "test(worker): segmenter + retriever fusion tests; eval + migrate harness

27/27 unit tests pass. Eval harness measures recall@k/MRR/nDCG for
dense vs hybrid vs hybrid+reranker. Migration script re-creates the
v2 collection and re-ingests the corpus."
```

---

## File Inventory (Changed/New, Uncommitted)

### Python (Worker) — Modified (10)
- `worker/app/services/loader.py` — +density probe, +run grouper, +page manifest, PDF branch rewritten
- `worker/app/services/retriever.py` — **full rewrite**: hybrid dense+BM25 RRF, tenant filter on both prefetches
- `worker/app/services/vectorstore.py` — **full rewrite**: v2 schema (named dense + text_segmented index), segmented upsert
- `worker/app/api/query.py` — **full rewrite**: HyDE + segment + hybrid retrieve + rerank pipeline
- `worker/app/api/ingest.py` — +page_routes aggregation + structured log line
- `worker/app/config.py` — +7 Phase 1 knobs (hybrid_rrf_k, prefetch_top_n, reranker_*, hyde_*)
- `worker/app/main.py` — +Reranker singleton, +settings on state, rrf_k wiring, version 0.2.0
- `worker/app/schemas/ingest.py` — +PageRoute model, +page_routes on IngestResponse
- `worker/app/services/test_loader_partial.py` — +5 Phase 0 tests, adapted legacy OR-merge test
- `worker/pyproject.toml` — +pyvi>=0.1.0

### Python (Worker) — New (7)
- `worker/app/services/segmenter.py` — pyvi Vietnamese word segmentation
- `worker/app/services/reranker.py` — bge-reranker-v2-m3 cross-encoder, lazy + RAM-guarded
- `worker/app/services/hyde.py` — HyDE hypothetical document generation
- `worker/app/services/test_segmenter.py` — 7 segmenter contract tests
- `worker/app/services/test_retriever_fusion.py` — 8 fusion + tenant-filter tests
- `RAG_test/eval_retrieval.py` — recall@k/MRR/nDCG eval harness
- `RAG_test/migrate_collection.py` — v2 collection migration + re-ingest

---

## What Was NOT Done (Explicitly Out of Scope — Phase 2+)

From the original audit roadmap, these remain for later phases:
- **NLI grounding filter** (mDeBERTa claim-vs-source check before streaming) — Phase 2
- **DB-enforced tenant isolation** (`HasQueryFilter` instead of manual `.Where()` in DocumentsController) — Phase 2. Current state: controller-level string filtering with an admin cross-tenant bypass hole.
- **Chunk-level provenance** (chunk-id + char-span, not doc-level `SourceDocumentIdsJson`) — Phase 2, required input for NLI
- **Delete orphaned `docx_processor.py` mammoth chunker** (dead code landmine) — Phase 3 cleanup. Only `convert_doc_to_docx` is live; the `DocxProcessor` class + mammoth chunker are orphaned.
- **`completion_tokens` fix** in `query.py` (currently counts stream deltas, not tokens) — Phase 3
- **.NET `Document` schema** for persisting the page manifest to DB — currently manifest is response+logs only

---

## Known Issues / Risks

1. **Qdrant version sensitivity:** the hybrid retriever uses `Prefetch`, `FusionQuery`, `Fusion.RRF`, and `query_points` — all require Qdrant client ≥1.10 and a compatible server. Verified the client API shapes at write time, but NOT against a live server.
2. **`query_text_segmented` empty-string `using` field:** the BM25 prefetch uses `using=""` (empty string = payload text index, per Qdrant docs). This is the documented convention but has not been runtime-verified against this server. If it errors, check Qdrant's full-text query API for the exact `using` value.
3. **Reranker memory:** ~2GB model alongside bge-m3's ~2GB on a RAM-constrained host. RAM-guard mitigates but may default-OFF in practice.
4. **`pyvi` segmentation quality on proper nouns / statute numbers** (e.g. `1202/QĐ-BKHCNCGCN`): untested against the real corpus. The eval will reveal if segmentation helps or hurts BM25 precision on these.
5. **Pre-existing test adapted:** `test_or_merge_across_digital_and_ocr_passes` was rewritten to match the new Phase 0 control flow (digital pass now goes through `_convert_pdf_page_ranges`). Intent preserved (OR-merge of failed ranges), but the mock surface changed.

---

## Architecture Quick Reference (Phase 1)

### Hybrid Retrieval Flow
```
Query (Vietnamese)
  │
  ├─[if hyde_enabled & short]─► LlmRouter generates hypothetical answer
  │                              └─► embed THAT as dense vector
  │
  ├─ segment(query)  ──────────► pyvi: "khách hàng" → "khách_hàng"
  │
  ▼
Retriever.search(query_vector, query_text_segmented, department_id)
  │
  ├─ Prefetch 1: dense cosine   (using="dense",  filter=tenant)
  ├─ Prefetch 2: BM25 fulltext  (using="",       filter=tenant)  ← SAME tenant filter
  │
  └─► Qdrant query_points(prefetch=[1,2], fusion=RRF, k=60)  ← server-side
        │
        ▼
      top-N candidates
        │
        ├─[if reranker_enabled & RAM ok]─► CrossEncoder rescore → top-k
        │
        ▼
      RetrievedSource[] → LLM context
```

### Why RRF, not weighted-score blending
Cosine ∈ [0,1] and BM25 ∈ [0,∞) are non-commensurable — naive weighted blending yields destructive noise (the "Search Noise Trap" from the audit). RRF operates on RANKS, not scores, so it sidesteps the scale mismatch entirely. k=60 is the standard value from Cormack et al. (2009).

### Tenant isolation invariant
The `department_id` MUST filter is applied to BOTH prefetch branches via `_tenant_filter()`. Unit test `test_tenant_filter_applied_to_both_prefetches` guards this — if either branch omits it, cross-tenant data can leak through RRF fusion.

---

## Verification Commands

```bash
cd worker

# Run the full unit test suite (no Qdrant needed)
.venv/Scripts/python.exe -m pytest app/services/ -v

# Syntax check all touched files
for f in app/services/loader.py app/services/segmenter.py app/services/retriever.py \
         app/services/reranker.py app/services/hyde.py app/services/vectorstore.py \
         app/api/query.py app/config.py app/main.py; do
  .venv/Scripts/python.exe -c "import ast; ast.parse(open('$f').read())" && echo "OK $f"
done

# After Qdrant is up — the open gate:
python -m RAG_test.migrate_collection --yes   # re-create + re-ingest
python -m RAG_test.eval_retrieval EVAL 5      # measure retrieval quality
```

---

## How to Resume

1. **Start Qdrant**, run `migrate_collection.py --yes`, then `eval_retrieval.py`.
2. **Read the eval numbers.** Decide: keep reranker default-ON? flip HyDE ON? Adjust `hybrid_rrf_k` or `prefetch_top_n`?
3. **Commit** using the split above (or one big commit — your call).
4. **If eval shows problems**, the most likely culprits are: (a) the BM25 `using=""` field value against your specific Qdrant server, (b) pyvi mis-segmenting statute numbers, (c) reranker OOM. Each has a documented fallback.
5. **Phase 2 candidates** (when ready): NLI grounding filter, DB-enforced tenant isolation, chunk-level provenance.
