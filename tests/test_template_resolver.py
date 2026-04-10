"""Tests for Work Item B: Resolve template references in documentation."""
from pathlib import Path

import pytest

from ingestion.template_resolver import resolve_templates, extract_template_refs


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


SAMPLE_DOC = """\
# First Steps

The simplest FastAPI file:

{* ../../docs_src/first_steps/tutorial001.py *}

Copy that to a file `main.py`.
"""

SAMPLE_DOC_WITH_HL = """\
# Settings

Import settings:

{* ../../docs_src/settings/tutorial001.py hl[2,5:8] *}

That's it.
"""

SAMPLE_SOURCE = """\
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
"""


class TestExtractTemplateRefs:
    def test_extracts_simple_ref(self) -> None:
        refs = extract_template_refs(SAMPLE_DOC)
        assert len(refs) == 1
        assert refs[0]["path"] == "../../docs_src/first_steps/tutorial001.py"
        assert refs[0]["highlight"] is None

    def test_extracts_ref_with_highlight(self) -> None:
        refs = extract_template_refs(SAMPLE_DOC_WITH_HL)
        assert len(refs) == 1
        assert refs[0]["path"] == "../../docs_src/settings/tutorial001.py"
        assert refs[0]["highlight"] == "hl[2,5:8]"

    def test_no_refs_in_plain_markdown(self) -> None:
        refs = extract_template_refs("# Hello\n\nNo templates here.\n")
        assert refs == []


class TestResolveTemplates:
    def test_inlines_code_when_source_exists(self, tmp_path: Path) -> None:
        doc_path = tmp_path / "docs" / "tutorial" / "guide.md"
        write(doc_path, SAMPLE_DOC)
        src_path = tmp_path / "docs_src" / "first_steps" / "tutorial001.py"
        write(src_path, SAMPLE_SOURCE)

        resolved = resolve_templates(doc_path.read_text(), doc_path)
        assert "{*" not in resolved
        assert "```python" in resolved
        assert "from fastapi import FastAPI" in resolved

    def test_marks_unavailable_when_source_missing(self, tmp_path: Path) -> None:
        doc_path = tmp_path / "docs" / "tutorial" / "guide.md"
        write(doc_path, SAMPLE_DOC)
        # Don't create the source file

        resolved = resolve_templates(doc_path.read_text(), doc_path)
        assert "{*" not in resolved
        assert "code reference" in resolved.lower() or "not available" in resolved.lower()

    def test_preserves_non_template_content(self, tmp_path: Path) -> None:
        doc_path = tmp_path / "docs" / "tutorial" / "guide.md"
        write(doc_path, SAMPLE_DOC)
        src_path = tmp_path / "docs_src" / "first_steps" / "tutorial001.py"
        write(src_path, SAMPLE_SOURCE)

        resolved = resolve_templates(doc_path.read_text(), doc_path)
        assert "# First Steps" in resolved
        assert "Copy that to a file" in resolved

    def test_handles_multiple_refs_in_one_doc(self, tmp_path: Path) -> None:
        content = (
            "# Doc\n\n"
            "{* ../docs_src/a.py *}\n\n"
            "Middle text.\n\n"
            "{* ../docs_src/b.py *}\n"
        )
        doc_path = tmp_path / "docs" / "multi.md"
        write(doc_path, content)
        write(tmp_path / "docs_src" / "a.py", "# file a\nprint('a')\n")
        write(tmp_path / "docs_src" / "b.py", "# file b\nprint('b')\n")

        resolved = resolve_templates(doc_path.read_text(), doc_path)
        assert "print('a')" in resolved
        assert "print('b')" in resolved
        assert "Middle text." in resolved
