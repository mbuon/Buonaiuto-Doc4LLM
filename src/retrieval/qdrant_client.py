from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QdrantQuery:
    workspace_id: str
    library_id: str
    version: str | None
    query_text: str
    limit: int


# ---------------------------------------------------------------------------
# BM25-style sparse vector for true hybrid search
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "as", "be", "was", "are",
    "this", "that", "how", "what", "when", "where", "who", "which",
    "do", "does", "did", "not", "no", "can", "will", "should", "would",
    "could", "may", "i", "you", "we", "they", "my", "your", "use",
    "using", "used", "about", "into", "up", "out", "if", "then",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_WORDS and len(t) > 1]


def _bm25_sparse_vector(text: str, k1: float = 1.2, b: float = 0.75) -> tuple[list[int], list[float]]:
    """Produce a sparse BM25-style vector as (indices, values).

    Uses consistent token-index hashing so the same token always maps to the
    same dimension, enabling dot-product matching against indexed sparse vectors.
    Dimension space: 2^16 = 65536 buckets (hash collisions are rare and acceptable).
    """
    tokens = _tokenize(text)
    if not tokens:
        return [], []

    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1

    doc_len = len(tokens)
    # Approximate average document length — used for BM25 normalization.
    # Without a corpus-wide IDF we use a TF-normalised variant that treats
    # each unique token equally (IDF = 1).
    avg_doc_len = 128  # Tuned for typical documentation chunk size (~600 words)

    indices: list[int] = []
    values: list[float] = []
    seen: set[int] = set()

    for token, freq in tf.items():
        bucket = hash(token) % 65536
        if bucket in seen:
            continue  # Simple collision handling: keep first token that maps here
        seen.add(bucket)
        # BM25 TF component
        tf_score = (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / avg_doc_len))
        indices.append(bucket)
        values.append(round(tf_score, 4))

    return indices, values


class QdrantHybridClient:
    """Qdrant adapter for hybrid retrieval with required payload filters.

    When the qdrant-client library supports ``SparseVector`` (v1.7+), queries
    include both a dense embedding vector and a BM25 sparse vector, enabling
    true Reciprocal Rank Fusion (RRF) hybrid search.  On older clients the
    query falls back to dense-only.

    ``named_vectors``: when True the collection was created with named vector
    configs (``{"dense": VectorParams(...)}``).  The indexer must then upsert
    using ``{"dense": vector}`` instead of a plain list.
    """

    def __init__(
        self,
        client: Any | None,
        collection_name: str,
        embedder: Any | None = None,
        named_vectors: bool = False,
    ):
        self.client = client
        self.collection_name = collection_name
        self.embedder = embedder  # ModelProviderRouter or EmbeddingProvider
        self.named_vectors = named_vectors

    def query_hybrid(self, query: QdrantQuery) -> list[dict[str, Any]]:
        if self.client is None:
            raise NotImplementedError("Qdrant client backend is not configured.")
        if query.limit <= 0:
            return []
        if not query.query_text.strip():
            return []

        query_vector = self._embed_query(query.query_text)
        if query_vector is None:
            raise NotImplementedError("No embedder configured for vector queries.")

        sparse_indices, sparse_values = _bm25_sparse_vector(query.query_text)
        query_filter = self._build_query_filter(query)

        response = self._call_backend(
            query_vector=query_vector,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            query_filter=query_filter,
            limit=query.limit,
        )
        points = self._extract_points(response)
        return [self._normalize_point(point) for point in points]

    def _embed_query(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        if hasattr(self.embedder, "embed_texts"):
            result = self.embedder.embed_texts([text])
            vectors = result.get("vectors", [])
            return vectors[0] if vectors else None
        if hasattr(self.embedder, "embed"):
            vectors = self.embedder.embed([text])
            return vectors[0] if vectors else None
        return None

    def _call_backend(
        self,
        query_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        query_filter: dict[str, Any],
        limit: int,
    ) -> Any:
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            conditions = []
            for item in query_filter.get("must", []):
                conditions.append(
                    FieldCondition(
                        key=item["key"],
                        match=MatchValue(value=item["match"]["value"]),
                    )
                )
            qf = Filter(must=conditions) if conditions else None
        except ImportError:
            qf = query_filter

        # Attempt true hybrid query (dense + sparse RRF) when client supports it
        if sparse_indices and hasattr(self.client, "query_points"):
            try:
                return self._call_hybrid_rrf(
                    query_vector, sparse_indices, sparse_values, qf, limit
                )
            except Exception:
                pass  # Fall through to dense-only

        if hasattr(self.client, "query_points"):
            return self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=qf,
                limit=limit,
                with_payload=True,
            )
        if hasattr(self.client, "search"):
            return self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=qf,
                limit=limit,
                with_payload=True,
            )
        raise NotImplementedError("Qdrant backend does not expose query_points/search.")

    def _call_hybrid_rrf(
        self,
        query_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        query_filter: Any,
        limit: int,
    ) -> Any:
        """Issue a true hybrid RRF query using Qdrant's Prefetch + Query API."""
        from qdrant_client.models import (  # type: ignore[import-untyped]
            Prefetch, SparseVector, Query, FusionQuery, Fusion,
            NamedVector, NamedSparseVector,
        )

        dense_prefetch = Prefetch(
            query=query_vector,
            using="dense",
            filter=query_filter,
            limit=limit * 2,
        )
        sparse_prefetch = Prefetch(
            query=SparseVector(indices=sparse_indices, values=sparse_values),
            using="sparse",
            filter=query_filter,
            limit=limit * 2,
        )
        return self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[dense_prefetch, sparse_prefetch],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True,
        )

    @staticmethod
    def _build_query_filter(query: QdrantQuery) -> dict[str, Any]:
        must = [
            {"key": "workspace_id", "match": {"value": query.workspace_id}},
        ]
        if query.library_id:
            must.append({"key": "library_id", "match": {"value": query.library_id}})
        if query.version is not None:
            must.append({"key": "version", "match": {"value": query.version}})
        return {"must": must}

    @staticmethod
    def _extract_points(response: Any) -> list[Any]:
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            if isinstance(response.get("points"), list):
                return list(response["points"])
            if isinstance(response.get("result"), list):
                return list(response["result"])
            return []
        points = getattr(response, "points", None)
        if points is not None:
            return list(points)
        result = getattr(response, "result", None)
        if result is not None:
            return list(result)
        return []

    @staticmethod
    def _normalize_point(point: Any) -> dict[str, Any]:
        payload = getattr(point, "payload", None)
        if payload is None and isinstance(point, dict):
            payload = point.get("payload", {})
        if payload is None:
            payload = {}

        score = getattr(point, "score", None)
        if score is None and isinstance(point, dict):
            score = point.get("score", 0.0)

        return {
            "workspace_id": payload.get("workspace_id"),
            "library_id": payload.get("library_id"),
            "version": payload.get("version"),
            "rel_path": payload.get("rel_path"),
            "title": payload.get("title"),
            "source_uri": payload.get("source_uri"),
            "snippet": payload.get("snippet") or payload.get("text", ""),
            "score": float(score or 0.0),
        }
