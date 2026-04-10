"""Tests for the 10 MCP server improvement recommendations."""
from pathlib import Path

import pytest

from buonaiuto_doc4llm.service import DocsHubService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


BIG_DOC = """# Stripe Payments

Introduction to payments.

## Accept a Payment

Use PaymentIntents to accept payments.

## Verify Events

Verify webhook signatures with the Stripe library.

```python
stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
```

## Handle Errors

Always handle card errors and API errors.

## Refunds

Issue refunds via the Refunds API.
"""


def _setup(tmp_path: Path, doc_content: str = BIG_DOC, tech: str = "stripe") -> DocsHubService:
    write(
        tmp_path / "docs_center/projects/app.json",
        f'{{"project_id":"app","name":"App","technologies":["{tech}"]}}',
    )
    write(
        tmp_path / f"docs_center/technologies/{tech}/manifest.json",
        f'{{"technology":"{tech}","version":"2025-01"}}',
    )
    write(tmp_path / f"docs_center/technologies/{tech}/docs/guide.md", doc_content)
    service = DocsHubService(tmp_path)
    service.scan()
    return service


# ── #1: Section-level reading ──

class TestSectionReading:
    def test_read_doc_with_section_returns_matching_section(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        result = service.read_doc("stripe", "docs/guide.md", section="Verify Events")
        assert "Verify webhook signatures" in result["content"]
        assert result["section_match"] == "Verify Events"

    def test_read_doc_section_not_found_raises(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        with pytest.raises(ValueError, match="Section not found"):
            service.read_doc("stripe", "docs/guide.md", section="Nonexistent Section")

    def test_read_doc_returns_toc_when_no_section(self, tmp_path: Path) -> None:
        # When document is truncated, include a TOC
        big = "# Title\n\nIntro.\n" + "\n".join(
            f"## Section {i}\n\n{'word ' * 5000}\n" for i in range(50)
        )
        service = _setup(tmp_path, big)
        result = service.read_doc("stripe", "docs/guide.md", max_tokens=2000)
        assert result["truncated"] is True
        assert "table_of_contents" in result
        assert len(result["table_of_contents"]) > 0
        assert "title" in result["table_of_contents"][0]


# ── #5: Doc size in search results ──

class TestSizeInSearchResults:
    def test_search_results_include_char_count(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        payload = service.search_docs("stripe", "payments", limit=5)
        results = payload["results"]
        assert len(results) > 0
        for r in results:
            assert "char_count" in r
            assert isinstance(r["char_count"], int)
            assert r["char_count"] > 0

    def test_search_documentation_results_include_char_count(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        payload = service.search_documentation(query="payments", libraries=[{"id": "stripe"}])
        for r in payload["results"]:
            assert "char_count" in r


# ── #9: Smart truncation with TOC ──

class TestSmartTruncationTOC:
    def test_truncated_response_includes_toc(self, tmp_path: Path) -> None:
        big = "# Title\n\nIntro.\n" + "\n".join(
            f"## Section {i}\n\n{'word ' * 5000}\n" for i in range(50)
        )
        service = _setup(tmp_path, big)
        result = service.read_doc("stripe", "docs/guide.md", max_tokens=2000)
        assert result["truncated"] is True
        toc = result["table_of_contents"]
        assert isinstance(toc, list)
        # TOC should have the section titles
        titles = [entry["title"] for entry in toc]
        assert "Title" in titles
        assert "Section 0" in titles

    def test_non_truncated_single_section_has_no_toc(self, tmp_path: Path) -> None:
        service = _setup(tmp_path, "# Small\n\nTiny doc with just one section.")
        result = service.read_doc("stripe", "docs/guide.md")
        assert result["truncated"] is False
        # Single-section docs don't need a TOC
        assert result["table_of_contents"] is None

    def test_non_truncated_multi_section_has_toc(self, tmp_path: Path) -> None:
        service = _setup(tmp_path, "# Title\n\nIntro.\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B.\n")
        result = service.read_doc("stripe", "docs/guide.md")
        assert result["truncated"] is False
        # Multi-section docs always get a TOC
        assert result["table_of_contents"] is not None
        titles = [e["title"] for e in result["table_of_contents"]]
        assert "Section A" in titles
        assert "Section B" in titles


# ── #10: Freshness metadata ──

class TestFreshnessMetadata:
    def test_read_doc_includes_last_scanned(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        result = service.read_doc("stripe", "docs/guide.md")
        assert "last_scanned_at" in result
        assert result["last_scanned_at"] is not None

    def test_search_results_include_last_scanned(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        payload = service.search_docs("stripe", "payments", limit=5)
        for r in payload["results"]:
            assert "last_scanned_at" in r


# ── #4: Locale awareness ──

class TestLocaleMetadata:
    def test_read_doc_returns_locale_metadata(self, tmp_path: Path) -> None:
        service = _setup(tmp_path)
        result = service.read_doc("stripe", "docs/guide.md")
        # Should have a locale field (detected or from manifest)
        assert "locale" in result
