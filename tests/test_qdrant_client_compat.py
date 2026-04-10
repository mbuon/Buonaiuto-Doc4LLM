from retrieval.qdrant_client import QdrantHybridClient, QdrantQuery


class _SearchBackend:
    def __init__(self) -> None:
        self.kwargs = None

    def search(self, **kwargs):
        self.kwargs = kwargs
        return []


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_qdrant_client_uses_filter_keyword_for_search_backend() -> None:
    backend = _SearchBackend()
    client = QdrantHybridClient(client=backend, collection_name="docs", embedder=_FakeEmbedder())
    client.query_hybrid(
        QdrantQuery(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            query_text="hooks",
            limit=3,
        )
    )

    assert backend.kwargs is not None
    assert "query_filter" in backend.kwargs
    assert backend.kwargs["query_filter"] is not None
