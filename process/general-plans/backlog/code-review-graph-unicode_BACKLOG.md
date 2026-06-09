# Backlog: code-review-graph Pre-commit Hook UnicodeEncodeError

**Priority:** Low
**Discovered:** 2026-06-09 (during hybrid-chunker implementation session)
**Status:** Open — non-blocking

---

## Problem

The `code-review-graph` pre-commit hook fails with a `UnicodeEncodeError` (cp1252 codec) on Windows when any staged file contains Vietnamese text.

Example error output:
```
UnicodeEncodeError: 'cp1252' codec can't encode character 'ă' in position N
```

The commit itself still succeeds. Only the hook's console output fails to print. No data is lost.

---

## Root Cause

Windows console codepage defaults to cp1252 (Western European). When the `code-review-graph` hook tries to print filenames or diff content that includes Vietnamese Unicode characters (e.g., `ă`, `ơ`, `ư`, `đ`), Python's stdout encoding rejects them.

This is a Windows-only issue. Linux/macOS use UTF-8 by default.

---

## Affected Surface

- `.git/hooks/pre-commit` (or equivalent hook runner)
- `code-review-graph` MCP server hook script
- Any Windows developer machine with Vietnamese-named files or Vietnamese content in staged diffs

---

## Fix Options

1. **Set `PYTHONIOENCODING=utf-8`** in the hook script environment before executing the code-review-graph hook binary. Lowest effort.
2. **Add `sys.stdout.reconfigure(encoding='utf-8')` / `errors='replace'`** at the top of the hook's Python entry point if source is accessible.
3. **Change Windows console codepage** to UTF-8 globally: `chcp 65001` in shell profile. User-level fix, not project-level.
4. **Wrap hook output in a try/except with `errors='replace'`** so encoding failures degrade gracefully instead of crashing the hook.

---

## Notes

- Non-blocking: commit workflow is unaffected. Fix when convenient.
- If the `code-review-graph` hook is updated or replaced, re-test on Windows with a Vietnamese-content file.
