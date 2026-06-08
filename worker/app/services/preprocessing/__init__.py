"""Document preprocessing — clean Markdown + overlap chunks for embedding."""

from .docx_processor import DocxProcessor, DocxProcessingError, ProcessedChunk

__all__ = ["DocxProcessor", "DocxProcessingError", "ProcessedChunk"]
