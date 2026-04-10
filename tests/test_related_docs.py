"""Tests for Work Item C: Related docs linking in read_doc."""
from pathlib import Path

from buonaiuto_doc4llm.service import DocsHubService, _extract_markdown_links


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup(tmp_path: Path) -> DocsHubService:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nLearn about [useState](useState.md) and [useEffect](useEffect.md).\n"
        "Also see [external](https://example.com/foo) link.\n",
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/useState.md",
        "# useState\n\nManage state in function components.\n",
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/useEffect.md",
        "# useEffect\n\nSide effects in components.\n",
    )
    write(
        tmp_path / "docs_center/technologies/react/docs/orphan.md",
        "# Orphan\n\nNot linked from anywhere.\n",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_extract_markdown_links_finds_relative_links() -> None:
    content = "See [foo](bar.md) and [baz](../qux.md) and [ext](https://example.com)."
    links = _extract_markdown_links(content)
    # Should find bar.md and ../qux.md but NOT https://example.com
    paths = [link["path"] for link in links]
    assert "bar.md" in paths
    assert "../qux.md" in paths
    assert "https://example.com" not in paths


def test_extract_markdown_links_captures_link_text() -> None:
    content = "Read [the guide](guide.md) for details."
    links = _extract_markdown_links(content)
    assert links[0]["text"] == "the guide"
    assert links[0]["path"] == "guide.md"


def test_read_doc_returns_related_docs(tmp_path: Path) -> None:
    service = _setup(tmp_path)
    result = service.read_doc("react", "docs/hooks.md")
    assert "related_docs" in result
    related = result["related_docs"]
    # Should find useState.md and useEffect.md (both exist in index)
    rel_paths = [r["rel_path"] for r in related]
    assert "docs/useState.md" in rel_paths
    assert "docs/useEffect.md" in rel_paths


def test_read_doc_excludes_external_links(tmp_path: Path) -> None:
    service = _setup(tmp_path)
    result = service.read_doc("react", "docs/hooks.md")
    related = result["related_docs"]
    # No external links
    for r in related:
        assert not r["rel_path"].startswith("http")


def test_read_doc_excludes_broken_links(tmp_path: Path) -> None:
    write(
        tmp_path / "docs_center/technologies/react/docs/broken.md",
        "# Broken\n\nSee [missing](nonexistent.md) for more.\n",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    result = service.read_doc("react", "docs/broken.md")
    # nonexistent.md doesn't exist in the index, so related_docs should be empty
    assert result["related_docs"] == []


def test_read_doc_related_docs_includes_title(tmp_path: Path) -> None:
    service = _setup(tmp_path)
    result = service.read_doc("react", "docs/hooks.md")
    related = result["related_docs"]
    use_state = next(r for r in related if r["rel_path"] == "docs/useState.md")
    assert use_state["title"] == "useState"


def test_read_doc_related_docs_capped_at_20(tmp_path: Path) -> None:
    """Documents with 30+ links should only return 20 related_docs."""
    links = "\n".join(f"- [Doc {i}](doc{i}.md)" for i in range(30))
    write(
        tmp_path / "docs_center/technologies/react/docs/many-links.md",
        f"# Many Links\n\n{links}\n",
    )
    for i in range(30):
        write(
            tmp_path / f"docs_center/technologies/react/docs/doc{i}.md",
            f"# Doc {i}\n\nContent.\n",
        )
    service = DocsHubService(tmp_path)
    service.scan()
    result = service.read_doc("react", "docs/many-links.md")
    assert len(result["related_docs"]) <= 20
