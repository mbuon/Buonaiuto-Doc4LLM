from pathlib import Path

import pytest

from buonaiuto_doc4llm.service import DocsHubService


def test_scan_returns_empty_when_technologies_root_missing(tmp_path: Path) -> None:
    service = DocsHubService(tmp_path)
    # docs_center/technologies intentionally not created
    result = service.scan()
    assert result["technologies"] == []
    assert result["total_documents"] == 0


def test_read_resource_rejects_malformed_doc_uri(tmp_path: Path) -> None:
    service = DocsHubService(tmp_path)
    with pytest.raises(ValueError, match="Malformed doc URI"):
        service.read_resource("doc://react")
