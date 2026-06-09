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

    # ---- Chunking ----
    # Larger windows preserve table/list rows with surrounding document context.
    chunk_size: int = 1024
    chunk_overlap: int = 250

    # ---- LLM (Ollama) ----
    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "gemma2:2b"
    ollama_timeout:      float = 120.0
    ollama_temperature:  float = 0.1

    # ---- Retrieval ----
    retrieval_top_k: int = 12

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
