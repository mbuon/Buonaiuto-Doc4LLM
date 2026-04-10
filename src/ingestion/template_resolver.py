"""Resolve template code references in documentation files.

FastAPI (and other MkDocs-based projects) use a syntax like:
    {* ../../docs_src/first_steps/tutorial001.py hl[1,3:5] *}

This module replaces those placeholders with the actual source code
as fenced code blocks. When the referenced file is not available
locally, the placeholder is replaced with a visible note.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Matches {* <path> *} and {* <path> hl[...] *}
_TEMPLATE_RE = re.compile(
    r'\{\*\s+'
    r'(?P<path>[^\s*]+)'
    r'(?:\s+(?P<highlight>hl\[[^\]]+\]))?'
    r'\s*\*\}'
)


def extract_template_refs(content: str) -> list[dict[str, Any]]:
    """Extract all template references from document content.

    Returns a list of dicts with keys: path, highlight, full_match.
    """
    refs: list[dict[str, Any]] = []
    for match in _TEMPLATE_RE.finditer(content):
        refs.append({
            "path": match.group("path"),
            "highlight": match.group("highlight"),
            "full_match": match.group(0),
        })
    return refs


def _infer_language(path: str) -> str:
    """Infer code fence language from file extension."""
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".sh": "bash",
        ".html": "html",
        ".css": "css",
    }.get(ext, "")


def resolve_templates(content: str, doc_path: Path) -> str:
    """Replace template references in content with actual source code.

    Args:
        content: The document text containing {* path *} placeholders.
        doc_path: Absolute path to the document file (used to resolve
                  relative paths in the template references).

    Returns:
        The content with all template references replaced.
    """
    doc_dir = doc_path.parent

    def _replace(match: re.Match) -> str:
        raw_path = match.group("path")
        highlight = match.group("highlight")
        source_path = (doc_dir / raw_path).resolve()

        if not source_path.is_file():
            return f"<!-- code reference: {raw_path} (not available locally) -->"

        try:
            code = source_path.read_text(encoding="utf-8").rstrip()
        except OSError:
            return f"<!-- code reference: {raw_path} (read error) -->"

        lang = _infer_language(raw_path)
        comment = ""
        if highlight:
            comment = f"  # highlighted: {highlight}"

        return f"```{lang}{comment}\n{code}\n```"

    return _TEMPLATE_RE.sub(_replace, content)
