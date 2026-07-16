"""Strongly-typed settings, loaded from environment / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime knobs for the worker. See `.env.example` for documentation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Inter-service auth ----
    worker_api_key: str

    # ---- Qdrant ----
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    # ---- Collection ----
    collection_name: str = "ld3_knowledge"
    vector_size: int = 1024  # bge-m3 dense dim

    # ---- Embedding ----
    embed_model: str = "BAAI/bge-m3"
    # bge-m3 supports up to 8192 tokens, but attention is O(seq^2) — at 8192 on
    # CPU it spikes multiple GB and can OOM a laptop. Our chunks are <=1024 words
    # (~1300 tokens worst case, median ~200), so cap well below the model max.
    embed_max_length: int = 1024
    # Sequences processed simultaneously. Lower = less peak RAM (trades speed).
    embed_batch_size: int = 4
    # CPU threads for torch. 0 = leave torch default. Cap on small machines to
    # stop thread oversubscription from ballooning memory.
    embed_torch_threads: int = 2

    # ---- Chunking ----
    # Larger windows preserve table/list rows with surrounding document context.
    chunk_size: int = 1024
    chunk_overlap: int = 250

    # ---- LLM (Ollama) ----
    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "qwen2.5:3b"
    ollama_timeout:      float = 120.0
    ollama_temperature:  float = 0.1
    # Caps Ollama's num_ctx (KV cache size) instead of letting it auto-size to
    # the model's full training max (32768 for qwen2.5:3b) — that oversized
    # allocation alone pushed even a 3B model into a 25%/75% CPU/GPU split on
    # a 4GB-VRAM card. Real RAG prompts here run well under this cap.
    ollama_num_ctx:      int = 8192

    # ---- Retrieval ----
    # Final top_k sent to the LLM. Full chunk texts reach the LLM (not the
    # 500-char snippet), so each hit can be up to ~1024 bge-m3 tokens.
    # Lowered 8->4 (07-2026): confirmed live that qwen2.5:3b refuses to answer
    # ("no info in database") even when the correct chunk with the exact
    # answer is ranked #1 — it gets lost among 7 unrelated distractor chunks.
    # Fewer, higher-precision chunks reduces that noise for a small model.
    retrieval_top_k: int = 4

    # ---- Phase 1: Hybrid search ----
    # RRF fusion constant k. 60 is the standard value (Cormack et al., 2009).
    # Higher = more weight to lower-ranked results in each branch.
    hybrid_rrf_k: int = 60
    # Number of candidates each prefetch (dense + BM25) retrieves before RRF.
    # Higher = better recall at the cost of more compute.
    prefetch_top_n: int = 50

    # ---- Phase 1: Reranker ----
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    # Minimum available RAM (GB) before loading the reranker model (~2 GB).
    # On machines with less free RAM, the reranker is skipped with a warning.
    reranker_ram_threshold_gb: float = 3.0

    # ---- Phase 1: HyDE (Hypothetical Document Embeddings) ----
    # Default OFF until the eval harness validates a precision gain.
    hyde_enabled: bool = False
    # Only generate HyDE for queries shorter than this (chars).
    # Long queries already carry enough semantic signal.
    hyde_max_query_chars: int = 80

    # ---- Redis / arq queue ----
    redis_host:     str        = "localhost"
    redis_port:     int        = 6379
    redis_db:       int        = 0
    redis_password: str | None = None

    # ---- Temporary upload landing zone (used by the /upload endpoint) ----
    tmp_uploads_dir: str = "tmp_uploads"

    # ---- Logging ----
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — instantiates Settings exactly once."""
    return Settings()  # type: ignore[call-arg]
