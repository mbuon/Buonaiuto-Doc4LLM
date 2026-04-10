"""Tests for the monolith file splitter (src/ingestion/splitter.py)."""
from pathlib import Path

from ingestion.splitter import split_monolith


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# 1. A file with 3 h1 sections splits into 3 files
# ------------------------------------------------------------------

def test_splits_three_h1_sections(tmp_path: Path) -> None:
    source = tmp_path / "llms-full.txt"
    source.write_text(
        "# Introduction\n\nSome intro text.\n\n"
        "# Getting Started\n\nHow to start.\n\n"
        "# API Reference\n\nThe API docs.\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    # Force split by setting threshold to 0
    result = split_monolith(source, out_dir, min_size_bytes=0)
    assert len(result) == 3
    assert all(p.exists() for p in result)


# ------------------------------------------------------------------
# 2. Heading text is slugified for filenames
# ------------------------------------------------------------------

def test_heading_slugified_for_filenames(tmp_path: Path) -> None:
    source = tmp_path / "llms-full.txt"
    source.write_text(
        "# Hello World!!\n\nfoo\n\n"
        "# API Reference (v2)\n\nbar\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    result = split_monolith(source, out_dir, min_size_bytes=0)
    names = sorted(p.name for p in result)
    assert "hello-world.md" in names
    assert "api-reference-v2.md" in names


# ------------------------------------------------------------------
# 3. Content between headings is preserved exactly
# ------------------------------------------------------------------

def test_content_preserved_exactly(tmp_path: Path) -> None:
    section_content = "Some detailed\nmultiline content\nwith code:\n```python\nprint('hi')\n```\n"
    source = tmp_path / "llms-full.txt"
    source.write_text(
        f"# First Section\n\n{section_content}"
        "# Second Section\n\nother stuff\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    result = split_monolith(source, out_dir, min_size_bytes=0)
    first_file = [p for p in result if p.name == "first-section.md"][0]
    content = first_file.read_text(encoding="utf-8")
    assert "# First Section" in content
    assert section_content.strip() in content


# ------------------------------------------------------------------
# 4. A file under the threshold (100KB) is not split
# ------------------------------------------------------------------

def test_file_under_threshold_not_split(tmp_path: Path) -> None:
    source = tmp_path / "small.txt"
    source.write_text("# Heading\n\nSmall file.\n", encoding="utf-8")
    out_dir = tmp_path / "output"
    # Default min_size_bytes=100_000 — this file is well under
    result = split_monolith(source, out_dir)
    assert result == []


# ------------------------------------------------------------------
# 5. A file with no h1 headings is not split (returns empty list)
# ------------------------------------------------------------------

def test_no_h1_headings_not_split(tmp_path: Path) -> None:
    source = tmp_path / "llms-full.txt"
    # Only h2 headings, no h1
    content = "## Sub Heading\n\nSome text.\n\n## Another\n\nMore text.\n"
    # Make it large enough to exceed threshold
    source.write_text(content * 5000, encoding="utf-8")
    out_dir = tmp_path / "output"
    result = split_monolith(source, out_dir, min_size_bytes=0)
    assert result == []


# ------------------------------------------------------------------
# 6. Integration: after splitting + scan(), each section is
#    individually searchable
# ------------------------------------------------------------------

def test_split_sections_individually_searchable(tmp_path: Path) -> None:
    from buonaiuto_doc4llm.service import DocsHubService

    # Set up project structure
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["mylib"]}',
    )
    write(
        tmp_path / "docs_center/technologies/mylib/manifest.json",
        '{"display_name":"My Lib"}',
    )

    # Write a monolith file and split it
    monolith = tmp_path / "docs_center/technologies/mylib/llms-full.txt"
    monolith.parent.mkdir(parents=True, exist_ok=True)
    monolith.write_text(
        "# Authentication\n\nUse JWT tokens for auth.\n\n"
        "# Database Setup\n\nConfigure PostgreSQL connection.\n\n"
        "# Deployment\n\nDeploy with Docker containers.\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "docs_center/technologies/mylib/docs"
    split_monolith(monolith, out_dir, min_size_bytes=0)

    # Now scan and search
    service = DocsHubService(tmp_path)
    service.scan()

    # Each section should be individually findable
    auth_payload = service.search_docs("mylib", "JWT tokens")
    assert any("authentication" in r["rel_path"].lower() for r in auth_payload["results"])

    db_payload = service.search_docs("mylib", "PostgreSQL")
    assert any("database" in r["rel_path"].lower() for r in db_payload["results"])


# ------------------------------------------------------------------
# 7. Duplicate heading slugs get numeric suffixes
# ------------------------------------------------------------------

def test_duplicate_heading_slugs_get_suffixes(tmp_path: Path) -> None:
    source = tmp_path / "llms-full.txt"
    source.write_text(
        "# Overview\n\nFirst overview.\n\n"
        "# Overview\n\nSecond overview.\n\n"
        "# Overview\n\nThird overview.\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output"
    result = split_monolith(source, out_dir, min_size_bytes=0)
    assert len(result) == 3
    names = sorted(p.name for p in result)
    assert "overview.md" in names
    assert "overview-2.md" in names
    assert "overview-3.md" in names
