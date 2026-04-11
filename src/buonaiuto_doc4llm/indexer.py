"""DocIndexer: chunk indexed documents, embed them, and upsert into Qdrant."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from ingestion.chunker import chunk_markdown
from retrieval.model_provider import ModelProviderRouter
from retrieval.qdrant_client import QdrantHybridClient

TEXT_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}


def _extract_title(path: Path, content: str) -> str:
    """Extract title from first heading line, or derive from filename."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem.replace("-", " ").replace("_", " ").title()

# NOTE: duplicated in service.py:extract_title — keep in sync


def _chunk_id(library_id: str, rel_path: str, chunk_index: int) -> str:
    """Deterministic UUID-style ID for a chunk (Qdrant accepts UUID strings)."""
    raw = f"{library_id}:{rel_path}:{chunk_index}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # Format as UUID: 8-4-4-4-12
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


class DocIndexer:
    """Reads text files from docs_center/technologies/<tech>/, chunks them,
    embeds each chunk, and upserts the resulting points into Qdrant.

    Qdrant errors are never swallowed — they propagate to the caller.
    """

    def __init__(
        self,
        technologies_root: Path | str,
        qdrant_client: QdrantHybridClient,
        embedder: ModelProviderRouter,
        workspace_id: str = "local",
    ) -> None:
        self.technologies_root = Path(technologies_root)
        self.qdrant_client = qdrant_client
        self.embedder = embedder
        self.workspace_id = workspace_id
        # Per-technology locks prevent interleaved Qdrant upserts from concurrent
        # scan_technology() calls, which can corrupt a local file-backed collection.
        self._tech_locks: dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    def index_technology(
        self,
        technology: str,
        rel_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Index one technology into Qdrant.

        Args:
            technology: the technology/library_id to index.
            rel_paths: if given, only re-index these specific relative paths.
                       If None, re-index every text file in the technology directory.

        Returns:
            Summary dict with ``technology``, ``chunks_indexed``, ``points_upserted``.

        Raises:
            RuntimeError: if Qdrant raises during upsert.
        """
        tech_dir = self.technologies_root / technology
        if not tech_dir.exists():
            return {"technology": technology, "chunks_indexed": 0, "points_upserted": 0}

        with self._locks_mutex:
            if technology not in self._tech_locks:
                self._tech_locks[technology] = threading.Lock()
            tech_lock = self._tech_locks[technology]

        with tech_lock:
            return self._index_technology_locked(technology, tech_dir, rel_paths)

    def _index_technology_locked(
        self,
        technology: str,
        tech_dir: Path,
        rel_paths: list[str] | None,
    ) -> dict[str, Any]:
        """Inner implementation of index_technology — called with the per-technology lock held."""
        files = self._collect_files(tech_dir, rel_paths)
        if not files:
            return {"technology": technology, "chunks_indexed": 0, "points_upserted": 0}

        all_points: list[dict[str, Any]] = []
        total_chunks = 0

        for file_path, rel_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
            except OSError:
                continue

            title = _extract_title(file_path, content)
            chunks = chunk_markdown(content)
            if not chunks:
                continue

            total_chunks += len(chunks)
            embed_result = self.embedder.embed_texts(chunks)
            vectors: list[list[float]] = embed_result.get("vectors", [])

            for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
                point = self._build_point(
                    technology=technology,
                    rel_path=rel_path,
                    title=title,
                    chunk=chunk,
                    chunk_index=i,
                    vector=vector,
                )
                all_points.append(point)

        if all_points:
            try:
                from qdrant_client.models import PointStruct
                qdrant_points = [
                    PointStruct(
                        id=p["id"],
                        vector=p["vector"],
                        payload=p["payload"],
                    )
                    for p in all_points
                ]
            except ImportError:
                qdrant_points = all_points

            self.qdrant_client.client.upsert(
                collection_name=self.qdrant_client.collection_name,
                points=qdrant_points,
            )

        return {
            "technology": technology,
            "chunks_indexed": total_chunks,
            "points_upserted": len(all_points),
        }

    def _collect_files(
        self, tech_dir: Path, rel_paths: list[str] | None
    ) -> list[tuple[Path, str]]:
        """Return (absolute_path, relative_path_str) pairs for text files to index."""
        if rel_paths is not None:
            result = []
            for rp in rel_paths:
                fp = tech_dir / rp
                if fp.exists() and fp.suffix in TEXT_EXTENSIONS:
                    result.append((fp, rp))
            return result

        result = []
        for fp in sorted(tech_dir.rglob("*")):
            if not fp.is_file():
                continue
            if fp.suffix not in TEXT_EXTENSIONS:
                continue
            rel = str(fp.relative_to(tech_dir))
            result.append((fp, rel))
        return result

    def _build_point(
        self,
        technology: str,
        rel_path: str,
        title: str,
        chunk: str,
        chunk_index: int,
        vector: list[float],
    ) -> dict[str, Any]:
        point_id = _chunk_id(technology, rel_path, chunk_index)
        return {
            "id": point_id,
            "vector": vector,
            "payload": {
                "workspace_id": self.workspace_id,
                "library_id": technology,
                "version": None,
                "rel_path": rel_path,
                "title": title,
                "source_uri": f"doc://{technology}/{rel_path}",
                "snippet": chunk[:400],
            },
        }
