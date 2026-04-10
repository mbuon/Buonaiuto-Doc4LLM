from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QdrantQuery:
    workspace_id: str
    library_id: str
    version: str | None
    query_text: str
    limit: int


class QdrantHybridClient:
    """Qdrant adapter for hybrid retrieval with required payload filters."""

    def __init__(
        self,
        client: Any | None,
        collection_name: str,
        embedder: Any | None = None,
    ):
        self.client = client
        self.collection_name = collection_name
        self.embedder = embedder  # ModelProviderRouter or EmbeddingProvider

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

        query_filter = self._build_query_filter(query)
        response = self._call_backend(
            query_vector=query_vector,
            query_filter=query_filter,
            limit=query.limit,
        )
        points = self._extract_points(response)
        return [self._normalize_point(point) for point in points]

    def _embed_query(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        # Support both ModelProviderRouter (embed_texts) and EmbeddingProvider (embed)
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
