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

    # ---- Retrieval ----
    # Full chunk texts now reach the LLM (not the 500-char snippet), so each hit can be up to
    # ~1024 bge-m3 tokens. Cap top_k at 8 → worst case ~8k tokens of context, which fits the
    # 8B/low-mid-server target (12 × 1024-token chunks would blow up the prompt).
    retrieval_top_k: int = 8

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
