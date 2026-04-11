"""Wire up Qdrant + embedding providers for semantic search.

Creates a local Qdrant instance (persistent on disk), configures
sentence-transformers or Ollama as embedding provider, and returns
a ready-to-use HybridRetriever and DocIndexer.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION_NAME = "buonaiuto_doc4llm"
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 produces 384-dim vectors


def create_qdrant_retriever_and_indexer(
    base_dir: Path,
    ollama_model: str = "nomic-embed-text",
) -> dict[str, Any]:
    """Build retriever + indexer backed by local Qdrant and embeddings.

    Tries providers in order:
      1. sentence-transformers (all-MiniLM-L6-v2) — preferred, fully offline
      2. Ollama (nomic-embed-text) — if ST not installed but Ollama available
      3. None — falls back to lexical-only search

    Qdrant uses local file storage at ``state/qdrant/`` (no server needed).

    Returns dict with keys: retriever, indexer, provider_name, qdrant_path
    """
    from retrieval.model_provider import (
        ModelProviderRouter,
        OllamaEmbeddingProvider,
    )
    from retrieval.qdrant_client import QdrantHybridClient
    from retrieval.retriever import HybridRetriever

    # -- Embedding providers --
    providers = []

    # Try sentence-transformers first
    try:
        from retrieval.sentence_transformers_provider import (
            SentenceTransformersEmbeddingProvider,
        )
        st_provider = SentenceTransformersEmbeddingProvider(
            name="sentence-transformers",
            model_name="all-MiniLM-L6-v2",
        )
        if st_provider.is_available():
            providers.append(st_provider)
            logger.info("sentence-transformers provider available (all-MiniLM-L6-v2)")
    except ImportError:
        pass

    # Ollama as fallback
    ollama_provider = OllamaEmbeddingProvider(
        name="ollama",
        model=ollama_model,
    )
    if ollama_provider.is_available():
        providers.append(ollama_provider)
        logger.info("Ollama embedding provider available (%s)", ollama_model)

    if not providers:
        logger.warning(
            "No embedding providers available. "
            "Install sentence-transformers or run Ollama for semantic search."
        )
        return {
            "retriever": HybridRetriever(),
            "indexer": None,
            "provider_name": None,
            "qdrant_path": None,
        }

    router = ModelProviderRouter(providers)
    active = router.select_provider()
    provider_name = active.name if active else None
    logger.info("Active embedding provider: %s", provider_name)

    # Detect embedding dimension from the active provider
    embedding_dim = _detect_embedding_dim(active)

    # -- Qdrant local storage --
    qdrant_path = base_dir / "state" / "qdrant"
    qdrant_path.mkdir(parents=True, exist_ok=True)
    _release_stale_qdrant_lock(qdrant_path)

    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        client = QdrantClient(path=str(qdrant_path))

        # Create collection if it doesn't exist
        collections = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in collections:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=embedding_dim,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(
                "Created Qdrant collection '%s' (dim=%d)",
                COLLECTION_NAME, embedding_dim,
            )
        else:
            logger.info("Qdrant collection '%s' already exists", COLLECTION_NAME)

    except ImportError:
        logger.warning("qdrant-client not installed — semantic search disabled")
        return {
            "retriever": HybridRetriever(),
            "indexer": None,
            "provider_name": provider_name,
            "qdrant_path": None,
        }
    except Exception as exc:
        logger.warning("Qdrant init failed: %s — falling back to lexical", exc)
        return {
            "retriever": HybridRetriever(),
            "indexer": None,
            "provider_name": provider_name,
            "qdrant_path": None,
        }

    qdrant_hybrid = QdrantHybridClient(
        client=client, collection_name=COLLECTION_NAME, embedder=router,
    )
    retriever = HybridRetriever(qdrant_client=qdrant_hybrid)

    from buonaiuto_doc4llm.indexer import DocIndexer
    technologies_root = base_dir / "docs_center" / "technologies"
    indexer = DocIndexer(
        technologies_root=technologies_root,
        qdrant_client=qdrant_hybrid,
        embedder=router,
        workspace_id="local",
    )

    return {
        "retriever": retriever,
        "indexer": indexer,
        "provider_name": provider_name,
        "qdrant_path": str(qdrant_path),
    }


def _release_stale_qdrant_lock(qdrant_path: Path) -> None:
    """Kill any process that holds the Qdrant lock file, then remove it.

    Qdrant uses a `.lock` file under its storage directory. When the server
    process is killed abruptly the lock is never released. This helper finds
    the holding process (via psutil or lsof), sends SIGTERM, waits up to 3 s
    for it to exit, and deletes the lock file so the next QdrantClient() call
    succeeds without operator intervention.
    """
    import os
    import signal
    import time

    lock_file = qdrant_path / ".lock"
    if not lock_file.exists():
        return

    pid: int | None = None

    # Prefer psutil (cross-platform)
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "open_files"]):
            try:
                for f in proc.open_files():
                    if f.path == str(lock_file):
                        pid = proc.pid
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            if pid:
                break
    except ImportError:
        pass

    # Fallback: lsof (macOS / Linux)
    if pid is None:
        import subprocess
        try:
            out = subprocess.check_output(
                ["lsof", "-t", str(lock_file)],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if out:
                pid = int(out.splitlines()[0])
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
            pass

    if pid is not None and pid != os.getpid():
        # Verify the process is actually Qdrant before killing it to avoid
        # accidentally terminating an unrelated process that happens to hold
        # the same lock path.
        is_qdrant = False
        try:
            import psutil
            proc = psutil.Process(pid)
            cmdline = " ".join(proc.cmdline()).lower()
            is_qdrant = "qdrant" in cmdline or "qdrant" in proc.name().lower()
        except Exception:
            # psutil unavailable or process already gone — check /proc as fallback
            try:
                cmdline_path = Path(f"/proc/{pid}/cmdline")
                if cmdline_path.exists():
                    raw = cmdline_path.read_bytes().replace(b"\x00", b" ").lower()
                    is_qdrant = b"qdrant" in raw
            except OSError:
                pass

        if not is_qdrant:
            logger.warning(
                "PID %d does not appear to be a Qdrant process — skipping kill, "
                "removing lock file only",
                pid,
            )
        else:
            logger.warning(
                "Releasing stale Qdrant lock held by PID %d — terminating process", pid
            )
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    time.sleep(0.1)
                    try:
                        os.kill(pid, 0)  # still alive?
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        lock_file.unlink(missing_ok=True)
        logger.info("Qdrant lock file removed: %s", lock_file)
    except OSError as exc:
        logger.warning("Could not remove Qdrant lock file: %s", exc)


def _detect_embedding_dim(provider: Any) -> int:
    """Detect vector dimension by embedding a test string."""
    if provider is None:
        return EMBEDDING_DIM
    try:
        vectors = provider.embed(["test"])
        if vectors and len(vectors[0]) > 0:
            dim = len(vectors[0])
            logger.info("Detected embedding dimension: %d", dim)
            return dim
    except Exception as exc:
        logger.warning("Could not detect embedding dim: %s, using default %d", exc, EMBEDDING_DIM)
    return EMBEDDING_DIM
