"""Renders the RAG system prompt by injecting retrieved sources into a Jinja template."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .retriever import RetrievedSource


class PromptBuilder:
    """Loads `prompts/rag_system.j2` once and renders it per request."""

    def __init__(self):
        templates_dir = Path(__file__).parent / "prompts"
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(enabled_extensions=(), default=False),  # plain-text prompts
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._system_template = self._env.get_template("rag_system.j2")

    def build_system(self, sources: Sequence[RetrievedSource]) -> str:
        """Returns the rendered system prompt with citations injected."""
        return self._system_template.render(sources=sources)
