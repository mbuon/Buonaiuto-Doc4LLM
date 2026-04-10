"""Tests for HttpDocFetcher auto-discovery integration."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from ingestion.http_fetcher import HttpDocFetcher
from ingestion.source_mapper import LibraryMapping


def _make_fetcher(tmp_path: Path) -> HttpDocFetcher:
    db_path = tmp_path / "state" / "docs_hub.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return HttpDocFetcher(
        base_dir=tmp_path,
        db_path=db_path,
        registry=[],  # empty registry — forces discovery
    )


def test_fetch_unknown_tech_triggers_discovery(tmp_path: Path) -> None:
    """When technology is not in registry, fetcher attempts auto-discovery."""
    fetcher = _make_fetcher(tmp_path)

    # Write a minimal registry for discover_and_register to write to
    registry_path = Path(__file__).resolve().parents[1] / "src" / "ingestion" / "registry.json"

    fake_sources = ["https://docs.example.com/llms-full.txt"]

    with patch("ingestion.doc_discovery.discover_and_register") as mock_discover, \
         patch.object(fetcher, "_do_fetch") as mock_do_fetch:

        mock_discover.return_value = {
            "discovered": True,
            "technology": "newlib",
            "sources": fake_sources,
            "registered": True,
        }
        mock_do_fetch.return_value = {
            "fetched": True,
            "technology": "newlib",
            "discovered": True,
        }

        result = fetcher.fetch("newlib")

    mock_discover.assert_called_once()
    assert result["fetched"] is True
    assert "newlib" in fetcher._by_id


def test_fetch_unknown_tech_raises_when_discovery_fails(tmp_path: Path) -> None:
    """When discovery finds nothing, fetch raises ValueError."""
    fetcher = _make_fetcher(tmp_path)

    with patch("ingestion.doc_discovery.discover_and_register") as mock_discover:
        mock_discover.return_value = {
            "discovered": False,
            "technology": "nonexistent",
            "sources": [],
            "registered": False,
        }

        try:
            fetcher.fetch("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "auto-discovery found no documentation sources" in str(exc)
