"""DOCX → clean Markdown → overlap chunks.

Pipeline:
    bytes (.docx) ── mammoth ──► raw Markdown (tables preserved as `|`)
                              │
                              ▼
                   regex-based junk scrubbing
                              │
                              ▼
            RecursiveCharacterTextSplitter (800 / 150)
                              │
                              ▼
                    list[ProcessedChunk]

Public surface:
    - ``DocxProcessor``       — class entry point.
    - ``ProcessedChunk``      — output dataclass.
    - ``DocxProcessingError`` — every non-trivial failure mode.

Caller pattern (fire-and-forget safe):

    proc = DocxProcessor(chunk_size=800, chunk_overlap=150)
    try:
        chunks = proc.process_file("policy.docx")
    except DocxProcessingError as err:
        logger.warning("docx_preprocess_failed file=%s err=%s", path, err)
        return []
    embeddings = embed_model.encode([c.text for c in chunks])
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
#  LibreOffice (soffice) integration — converts legacy .doc → .docx
#  on the fly so the rest of the pipeline can stay mammoth-only.
# ---------------------------------------------------------------------

# Hard ceiling on a single soffice invocation. .doc files of any sane
# size convert in well under 30 s; 120 s is comfortable headroom.
SOFFICE_TIMEOUT_SECONDS: int = int(os.environ.get("SOFFICE_TIMEOUT_SECONDS", "120"))


def _find_soffice_binary() -> str | None:
    """Locate the LibreOffice headless binary.

    Resolution order:
      1. ``SOFFICE_BIN`` env var (explicit override).
      2. ``soffice`` / ``libreoffice`` on ``PATH``.
      3. Known Windows default install path.

    Returns ``None`` if nothing is found — caller raises a clear error.
    """
    env = os.environ.get("SOFFICE_BIN")
    if env and Path(env).is_file():
        return env

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    if os.name == "nt":
        win_default = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
        if win_default.is_file():
            return str(win_default)

    return None


def convert_doc_to_docx(input_path: Path) -> Path:
    """Convert a legacy ``.doc`` file to ``.docx`` via LibreOffice headless.

    The output lives in a fresh temp directory (per call) — caller MUST
    delete that directory (``output_path.parent``) once it is done with
    the result. Concurrent calls are safe: each invocation runs with its
    own LibreOffice user-profile directory inside the temp dir, so two
    workers cannot collide on the global profile.

    Args:
        input_path: Existing on-disk ``.doc`` file.

    Returns:
        Path to the produced ``.docx``.

    Raises:
        DocxProcessingError: soffice missing / non-zero exit / timeout /
                             no output file produced.
    """
    if not input_path.is_file():
        raise DocxProcessingError(f"convert_doc_to_docx: source not found: {input_path}")

    soffice = _find_soffice_binary()
    if soffice is None:
        raise DocxProcessingError(
            "LibreOffice (soffice) is required to read .doc files but was not found. "
            "Install LibreOffice or set the SOFFICE_BIN environment variable."
        )

    out_dir       = Path(tempfile.mkdtemp(prefix="ld3_soffice_"))
    user_profile  = out_dir / "_uinst"
    user_profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        soffice,
        "--headless",
        "--nologo",
        "--norestore",
        # Per-call user profile → safe for concurrent invocations.
        f"-env:UserInstallation={user_profile.as_uri()}",
        "--convert-to", "docx",
        "--outdir",     str(out_dir),
        str(input_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SOFFICE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise DocxProcessingError(
            f"soffice binary not executable at {soffice}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise DocxProcessingError(
            f"soffice conversion timed out after {SOFFICE_TIMEOUT_SECONDS}s "
            f"on {input_path.name}."
        ) from exc

    if result.returncode != 0:
        stderr_tail = (result.stderr or result.stdout or "").strip()[-500:]
        shutil.rmtree(out_dir, ignore_errors=True)
        raise DocxProcessingError(
            f"soffice conversion failed (rc={result.returncode}): {stderr_tail}"
        )

    # Expected output name: <stem>.docx. Fall back to any .docx if soffice
    # normalised the filename (Vietnamese filenames sometimes get rewritten).
    expected = out_dir / (input_path.stem + ".docx")
    if expected.is_file():
        return expected

    candidates = [p for p in out_dir.glob("*.docx") if p.is_file()]
    if not candidates:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise DocxProcessingError(
            f"soffice produced no .docx output for {input_path.name}."
        )
    return candidates[0]


# ---------------------------------------------------------------------
#  Errors + result type
# ---------------------------------------------------------------------

class DocxProcessingError(Exception):
    """Raised when a DOCX cannot be processed (corrupt, unsupported, empty…)."""


@dataclass(frozen=True)
class ProcessedChunk:
    """One ready-to-embed slice of a document."""
    text:        str
    chunk_index: int
    metadata:    dict

    def to_dict(self) -> dict:
        return {"text": self.text, "chunk_index": self.chunk_index, **self.metadata}


# ---------------------------------------------------------------------
#  Processor
# ---------------------------------------------------------------------

class DocxProcessor:
    """End-to-end DOCX preprocessing: parse → clean → chunk."""

    # Patterns we have actually seen in extracted Vietnamese gov DOCX output.
    # The broader [a-z]{2}[A-Z][<$#@] rule catches the whole family
    # (gdY<, ytY<, kdP$, …) even if a new variant appears.
    DEFAULT_JUNK_PATTERNS: tuple[str, ...] = (
        r"gdY<",
        r"ytY<",
        r"kdP\$",
        r"[a-z]{2}[A-Z][<$#@]",                  # generic family
        r"(?:[a-z]{2}[A-Z][<$#@]\s*){2,}",        # repeated runs / formatting bursts
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",      # control chars (keep \t \n \r)
    )

    # File extensions we accept. `.doc` is converted to `.docx` first
    # (via LibreOffice headless) and then walks the standard pipeline.
    SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".docx", ".doc"})

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 150,
        junk_patterns: Iterable[str] | None = None,
    ):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be in [0, chunk_size)")

        self._chunk_size    = chunk_size
        self._chunk_overlap = chunk_overlap

        patterns = tuple(junk_patterns) if junk_patterns is not None else self.DEFAULT_JUNK_PATTERNS
        self._junk_regex = (
            re.compile("|".join(f"(?:{p})" for p in patterns))
            if patterns else None
        )

        # Whitespace tidy-up — pre-compile.
        self._trim_trailing_ws = re.compile(r"[ \t]+\n")
        self._collapse_spaces  = re.compile(r"[ \t]{2,}")
        self._collapse_blanks  = re.compile(r"\n{3,}")

        # Splitter is heavy — lazy-load on first chunk().
        self._splitter = None

    # =================================================================
    #  Public API
    # =================================================================

    def process_file(self, file_path: str | Path) -> list[ProcessedChunk]:
        """Read a DOCX from disk and run the full pipeline."""
        path = Path(file_path)
        if not path.is_file():
            raise DocxProcessingError(f"File not found: {path}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise DocxProcessingError(f"Cannot read {path}: {exc}") from exc
        return self.process_bytes(content, path.name)

    def process_bytes(self, content: bytes, file_name: str) -> list[ProcessedChunk]:
        """Run the full pipeline on in-memory DOCX bytes.

        ``.doc`` inputs are transparently routed through LibreOffice
        headless first — see :func:`convert_doc_to_docx`. Both the
        intermediate ``.doc`` and the generated ``.docx`` temp files are
        deleted on every exit path (success, parse failure, or crash).
        """
        if not content:
            raise DocxProcessingError("Empty file content.")

        ext = Path(file_name).suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise DocxProcessingError(f"Unsupported file extension: {ext}")

        logger.debug("docx_preprocess_begin file=%s ext=%s bytes=%d",
                     file_name, ext, len(content))

        # -------------------------------------------------------------
        # Middleware: .doc → .docx via LibreOffice headless.
        # Wrapped in try/finally so BOTH the input .doc temp file and
        # the soffice output directory are cleaned up — even on error.
        # -------------------------------------------------------------
        doc_tmp_dir:  Path | None = None
        docx_tmp_dir: Path | None = None
        try:
            if ext == ".doc":
                doc_tmp_dir = Path(tempfile.mkdtemp(prefix="ld3_doc_"))
                # Strip path separators just in case file_name carries them.
                safe_doc_name = Path(file_name).name or "input.doc"
                doc_path = doc_tmp_dir / safe_doc_name
                doc_path.write_bytes(content)

                docx_path     = convert_doc_to_docx(doc_path)
                docx_tmp_dir  = docx_path.parent
                content       = docx_path.read_bytes()
                file_name     = docx_path.name      # use the new name for logging
                ext           = ".docx"

                logger.info(
                    "doc_to_docx_ok original=%s new_bytes=%d",
                    safe_doc_name, len(content),
                )

            # -------------------------------------------------------------
            # Standard .docx pipeline.
            # -------------------------------------------------------------
            markdown = self._docx_to_markdown(content)
            if not markdown.strip():
                raise DocxProcessingError("Document contains no extractable text.")

            cleaned  = self._clean_text(markdown)
            if not cleaned.strip():
                raise DocxProcessingError("All content was scrubbed by the junk filter.")

            chunks   = self._chunk(cleaned)
            if not chunks:
                raise DocxProcessingError("Splitter produced no chunks.")

            logger.info(
                "docx_preprocess_ok file=%s md_chars=%d clean_chars=%d chunks=%d",
                file_name, len(markdown), len(cleaned), len(chunks),
            )

            return [
                ProcessedChunk(
                    text        = text,
                    chunk_index = i,
                    metadata    = {
                        "source":           file_name,
                        "chunk_size_chars": len(text),
                        "total_chunks":     len(chunks),
                    },
                )
                for i, text in enumerate(chunks)
            ]
        finally:
            # CRITICAL — always wipe BOTH temp directories. ignore_errors
            # because a clean-up failure must not mask a real upstream
            # error (and the OS will sweep /tmp later anyway).
            if doc_tmp_dir is not None:
                shutil.rmtree(doc_tmp_dir,  ignore_errors=True)
            if docx_tmp_dir is not None:
                shutil.rmtree(docx_tmp_dir, ignore_errors=True)

    # =================================================================
    #  Step 1 — DOCX → Markdown (tables preserved as `|` pipe form)
    # =================================================================

    @staticmethod
    def _docx_to_markdown(content: bytes) -> str:
        try:
            import mammoth
        except ImportError as exc:
            raise DocxProcessingError(
                "Dependency missing: mammoth.  Install with `pip install mammoth`."
            ) from exc

        try:
            result = mammoth.convert_to_markdown(io.BytesIO(content))
        except Exception as exc:                                       # noqa: BLE001
            raise DocxProcessingError(f"mammoth conversion failed: {exc}") from exc

        # Mammoth surfaces non-fatal warnings (unsupported style, etc.).
        for msg in result.messages:
            logger.debug("mammoth_msg type=%s body=%s", msg.type, msg.message)

        return result.value

    # =================================================================
    #  Step 2 — regex cleaning
    # =================================================================

    def _clean_text(self, text: str) -> str:
        if self._junk_regex is not None:
            text = self._junk_regex.sub("", text)

        # Collapse whitespace artefacts left behind by the substitutions.
        text = self._trim_trailing_ws.sub("\n",  text)
        text = self._collapse_spaces .sub(" ",   text)
        text = self._collapse_blanks .sub("\n\n",text)

        return text.strip()

    # =================================================================
    #  Step 3 — overlap chunking
    # =================================================================

    def _chunk(self, text: str) -> list[str]:
        splitter = self._get_splitter()
        return [c.strip() for c in splitter.split_text(text) if c.strip()]

    def _get_splitter(self):
        if self._splitter is not None:
            return self._splitter

        # Try the modular package first (langchain >= 0.2); fall back to legacy.
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError:
            try:
                from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore
            except ImportError as exc:
                raise DocxProcessingError(
                    "Dependency missing: langchain_text_splitters.  "
                    "Install with `pip install langchain-text-splitters`."
                ) from exc

        # Separators in priority order — most natural cut first.
        # `\n` covers both inter-line breaks and markdown table-row
        # boundaries, so tables tend to survive the split.
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size      = self._chunk_size,
            chunk_overlap   = self._chunk_overlap,
            length_function = len,
            is_separator_regex = False,
            separators = [
                "\n\n",   # paragraph break
                "\n",     # line break / markdown table row boundary
                "。",     # CJK sentence
                ". ",     # Latin sentence
                "! ",
                "? ",
                "; ",
                ", ",
                " ",
                "",       # last resort: hard cut
            ],
        )
        return self._splitter


# ---------------------------------------------------------------------
#  Manual CLI test:  python -m app.services.preprocessing.docx_processor file.docx
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m app.services.preprocessing.docx_processor <file.docx>")
        sys.exit(2)

    proc = DocxProcessor()
    try:
        out = proc.process_file(sys.argv[1])
    except DocxProcessingError as err:
        print(f"[FAIL] {err}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] {len(out)} chunks")
    for c in out[:3]:
        preview = c.text[:120].replace("\n", " ")
        print(f"  #{c.chunk_index:03d} ({c.metadata['chunk_size_chars']} chars): {preview}…")
