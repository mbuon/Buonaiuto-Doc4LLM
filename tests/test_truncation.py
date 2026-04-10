from pathlib import Path

from buonaiuto_doc4llm.service import DocsHubService, _estimate_tokens, _truncate_to_token_budget


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_service(tmp_path: Path, doc_content: str) -> DocsHubService:
    write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    write(tmp_path / "docs_center/technologies/react/docs/big.md", doc_content)
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_small_doc_not_truncated(tmp_path: Path) -> None:
    service = _setup_service(tmp_path, "# Small doc\n\nJust a few words.")
    result = service.read_doc("react", "docs/big.md")
    assert result["truncated"] is False
    assert "Small doc" in result["content"]


def test_large_doc_truncated_to_budget(tmp_path: Path) -> None:
    # Build a doc with many sections, ~100K chars total
    sections = ["# Big Document\n\nIntro paragraph.\n"]
    for i in range(200):
        sections.append(f"## Section {i}\n\n{'Lorem ipsum dolor sit amet. ' * 50}\n")
    big_content = "\n".join(sections)
    assert len(big_content) > 100_000

    service = _setup_service(tmp_path, big_content)
    result = service.read_doc("react", "docs/big.md", max_tokens=5000)
    assert result["truncated"] is True
    # The main content (before the TOC appendix) should respect the budget.
    # The TOC listing adds extra chars — split on the "---" separator.
    main_content = result["content"].split("\n\n---\n")[0]
    assert len(main_content) < 5000 * 4 + 500  # budget + metadata note
    assert "Big Document" in result["content"]  # first section always kept
    assert "omitted" in result["content"]
    assert result["table_of_contents"] is not None


def test_query_prioritizes_relevant_sections(tmp_path: Path) -> None:
    sections = [
        "# Documentation\n\nOverview.\n",
        "## Installing\n\nRun npm install.\n",
        "## Hooks\n\nuseState is the most important hook for managing state.\n",
        "## Routing\n\nUse react-router for client-side routing.\n",
    ]
    # Pad each section to make the doc exceed a small budget
    padded = []
    for s in sections:
        padded.append(s + ("filler text. " * 300) + "\n")
    big_content = "\n".join(padded)

    service = _setup_service(tmp_path, big_content)
    result = service.read_doc("react", "docs/big.md", max_tokens=2000, query="hooks useState")
    assert result["truncated"] is True
    # The hooks section should be prioritized
    assert "useState" in result["content"]


def test_max_tokens_none_returns_full(tmp_path: Path) -> None:
    sections = ["# Doc\n\n" + ("word " * 30000)]
    service = _setup_service(tmp_path, sections[0])
    result = service.read_doc("react", "docs/big.md", max_tokens=None)
    assert result["truncated"] is False
    assert result["total_tokens"] > 0


def test_truncate_to_token_budget_unit() -> None:
    text, truncated, toc, omitted = _truncate_to_token_budget("short", 1000)
    assert truncated is False
    assert text == "short"
    assert toc is None
    assert omitted == 0


def test_estimate_tokens() -> None:
    assert _estimate_tokens("abcd") == 1
    assert _estimate_tokens("a" * 400) == 100
