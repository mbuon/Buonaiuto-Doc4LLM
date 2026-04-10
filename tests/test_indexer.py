"""Tests for buonaiuto_doc4llm.indexer — DocIndexer chunks, embeds, and upserts into Qdrant."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from buonaiuto_doc4llm.indexer import DocIndexer
from retrieval.model_provider import DeterministicLocalEmbeddingProvider, ModelProviderRouter


def _make_indexer(tmp_path: Path, qdrant_client: MagicMock | None = None) -> tuple[DocIndexer, Path]:
    tech_root = tmp_path / "technologies"
    tech_root.mkdir(parents=True)

    if qdrant_client is None:
        qdrant_client = MagicMock()
        qdrant_client.client = MagicMock()
        qdrant_client.collection_name = "docs"

    embedder = ModelProviderRouter(
        providers=[DeterministicLocalEmbeddingProvider(name="deterministic")]
    )
    indexer = DocIndexer(
        technologies_root=tech_root,
        qdrant_client=qdrant_client,
        embedder=embedder,
        workspace_id="local",
    )
    return indexer, tech_root


class TestDocIndexerBasic:
    def test_index_technology_returns_summary(self, tmp_path: Path) -> None:
        indexer, tech_root = _make_indexer(tmp_path)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "llms-full.txt").write_text(
            "# React Hooks\n\nUse useState for local state.\n", encoding="utf-8"
        )

        result = indexer.index_technology("react")

        assert result["technology"] == "react"
        assert result["chunks_indexed"] >= 1
        assert "points_upserted" in result

    def test_index_technology_calls_qdrant_upsert(self, tmp_path: Path) -> None:
        fake_qdrant = MagicMock()
        fake_qdrant.client = MagicMock()
        fake_qdrant.collection_name = "docs"
        indexer, tech_root = _make_indexer(tmp_path, qdrant_client=fake_qdrant)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "llms-full.txt").write_text(
            "# React\n\nContent here.", encoding="utf-8"
        )

        indexer.index_technology("react")

        fake_qdrant.client.upsert.assert_called()
        call_args = fake_qdrant.client.upsert.call_args
        assert call_args[1]["collection_name"] == "docs" or call_args[0][0] == "docs"

    def test_index_technology_point_payload_has_required_fields(self, tmp_path: Path) -> None:
        upserted_points = []

        fake_qdrant = MagicMock()
        fake_qdrant.collection_name = "docs"
        fake_qdrant.client = MagicMock()

        def capture_upsert(**kwargs: object) -> None:
            upserted_points.extend(kwargs.get("points", []))

        fake_qdrant.client.upsert.side_effect = capture_upsert

        indexer, tech_root = _make_indexer(tmp_path, qdrant_client=fake_qdrant)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "guide.md").write_text(
            "# Getting Started\n\nInstall React with npm.", encoding="utf-8"
        )

        indexer.index_technology("react")

        assert len(upserted_points) >= 1
        point = upserted_points[0]
        payload = getattr(point, "payload", None) or point.get("payload")
        assert payload["library_id"] == "react"
        assert payload["workspace_id"] == "local"
        assert "rel_path" in payload
        assert "title" in payload
        assert "snippet" in payload

    def test_index_technology_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        indexer, _ = _make_indexer(tmp_path)

        result = indexer.index_technology("nonexistent-library")

        assert result["chunks_indexed"] == 0
        assert result["points_upserted"] == 0

    def test_index_technology_propagates_qdrant_errors(self, tmp_path: Path) -> None:
        fake_qdrant = MagicMock()
        fake_qdrant.collection_name = "docs"
        fake_qdrant.client = MagicMock()
        fake_qdrant.client.upsert.side_effect = RuntimeError("Qdrant collection not found")

        indexer, tech_root = _make_indexer(tmp_path, qdrant_client=fake_qdrant)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "llms-full.txt").write_text("# React\nContent.", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Qdrant"):
            indexer.index_technology("react")

    def test_index_technology_only_indexes_text_files(self, tmp_path: Path) -> None:
        fake_qdrant = MagicMock()
        fake_qdrant.collection_name = "docs"
        fake_qdrant.client = MagicMock()
        upserted_points: list = []
        fake_qdrant.client.upsert.side_effect = lambda **kw: upserted_points.extend(kw.get("points", []))

        indexer, tech_root = _make_indexer(tmp_path, qdrant_client=fake_qdrant)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "docs.md").write_text("# React\nContent.", encoding="utf-8")
        (tech_root / "react" / "image.png").write_bytes(b"\x89PNG\r\n")

        indexer.index_technology("react")

        paths = {(getattr(p, "payload", None) or p.get("payload"))["rel_path"] for p in upserted_points}
        assert all(not p.endswith(".png") for p in paths)

    def test_index_technology_with_rel_paths_filter(self, tmp_path: Path) -> None:
        fake_qdrant = MagicMock()
        fake_qdrant.collection_name = "docs"
        fake_qdrant.client = MagicMock()
        upserted_points: list = []
        fake_qdrant.client.upsert.side_effect = lambda **kw: upserted_points.extend(kw.get("points", []))

        indexer, tech_root = _make_indexer(tmp_path, qdrant_client=fake_qdrant)
        (tech_root / "react").mkdir()
        (tech_root / "react" / "hooks.md").write_text("# Hooks\nContent.", encoding="utf-8")
        (tech_root / "react" / "server.md").write_text("# Server\nContent.", encoding="utf-8")

        indexer.index_technology("react", rel_paths=["hooks.md"])

        paths = {(getattr(p, "payload", None) or p.get("payload"))["rel_path"] for p in upserted_points}
        assert "hooks.md" in paths
        assert "server.md" not in paths
