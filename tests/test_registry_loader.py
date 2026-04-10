"""Tests for ingestion.registry_loader — failing first, then passing after implementation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.registry_loader import load_registry
from ingestion.source_mapper import LibraryMapping


def test_loads_registry_returns_library_mappings(tmp_path: Path) -> None:
    registry = {
        "version": 1,
        "libraries": [
            {
                "library_id": "react",
                "display_name": "React",
                "package_names": ["react", "react-dom"],
                "sources": [
                    "https://react.dev/llms-full.txt",
                    "https://react.dev/llms.txt",
                ],
            }
        ],
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(registry), encoding="utf-8")

    mappings = load_registry(p)

    assert len(mappings) == 1
    assert isinstance(mappings[0], LibraryMapping)
    assert mappings[0].library_id == "react"
    assert "react" in mappings[0].package_names
    assert "react-dom" in mappings[0].package_names
    assert "https://react.dev/llms-full.txt" in mappings[0].sources


def test_preferred_source_selects_llms_full_txt(tmp_path: Path) -> None:
    from ingestion.source_mapper import CanonicalSourceMapper

    registry = {
        "version": 1,
        "libraries": [
            {
                "library_id": "nextjs",
                "display_name": "Next.js",
                "package_names": ["next"],
                "sources": [
                    "https://nextjs.org/llms.txt",
                    "https://nextjs.org/llms-full.txt",
                ],
            }
        ],
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(registry), encoding="utf-8")

    mappings = load_registry(p)
    preferred = CanonicalSourceMapper.preferred_source(mappings[0].sources)

    assert preferred == "https://nextjs.org/llms-full.txt"


def test_multiple_libraries_all_loaded(tmp_path: Path) -> None:
    registry = {
        "version": 1,
        "libraries": [
            {
                "library_id": "react",
                "display_name": "React",
                "package_names": ["react"],
                "sources": ["https://react.dev/llms.txt"],
            },
            {
                "library_id": "nextjs",
                "display_name": "Next.js",
                "package_names": ["next"],
                "sources": ["https://nextjs.org/llms.txt"],
            },
        ],
    }
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(registry), encoding="utf-8")

    mappings = load_registry(p)

    assert len(mappings) == 2
    ids = {m.library_id for m in mappings}
    assert ids == {"react", "nextjs"}


def test_missing_file_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_registry(Path("/nonexistent/path/registry.json"))


def test_invalid_json_raises_value_error(tmp_path: Path) -> None:
    p = tmp_path / "registry.json"
    p.write_text("not json at all", encoding="utf-8")

    with pytest.raises(ValueError, match="registry"):
        load_registry(p)


def test_seed_registry_loads_without_error() -> None:
    """The bundled registry.json must be parseable and non-empty."""
    seed_path = Path(__file__).parent.parent / "src" / "ingestion" / "registry.json"
    mappings = load_registry(seed_path)
    assert len(mappings) >= 5
    ids = {m.library_id for m in mappings}
    assert "react" in ids
    assert "nextjs" in ids
