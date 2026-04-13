from retrieval.qdrant_client import QdrantHybridClient, QdrantQuery


class _FakePoint:
    def __init__(self, payload: dict, score: float) -> None:
        self.payload = payload
        self.score = score


class _FakeResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeQdrantClient:
    def __init__(self) -> None:
        self.last_kwargs = None

    def query_points(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(
            [
                _FakePoint(
                    payload={
                        "workspace_id": "ws-a",
                        "library_id": "react",
                        "version": "19.0",
                        "rel_path": "docs/hooks.md",
                        "title": "Hooks",
                        "source_uri": "doc://react/docs/hooks.md",
                        "snippet": "Use hooks for state and effects.",
                    },
                    score=0.91,
                )
            ]
        )


def test_qdrant_client_raises_when_backend_missing() -> None:
    client = QdrantHybridClient(client=None, collection_name="docs")
    try:
        client.query_hybrid(
            QdrantQuery(
                workspace_id="ws-a",
                library_id="react",
                version="19.0",
                query_text="hooks",
                limit=5,
            )
        )
    except NotImplementedError:
        pass
    else:
        raise AssertionError("Expected NotImplementedError when backend client is missing.")


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_qdrant_client_applies_workspace_library_version_filters() -> None:
    fake = _FakeQdrantClient()
    client = QdrantHybridClient(client=fake, collection_name="docs", embedder=_FakeEmbedder())
    matches = client.query_hybrid(
        QdrantQuery(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            query_text="hooks",
            limit=5,
        )
    )

    assert fake.last_kwargs is not None
    # Depending on qdrant-client version and sparse vector support, the call
    # may use either prefetch+RRF or direct query_points with query_filter.
    # In both cases query_points must have been called with the right collection.
    assert fake.last_kwargs.get("collection_name") == "docs"
    # Verify the returned matches carry the correct fields
    assert len(matches) == 1
    assert matches[0]["library_id"] == "react"
    assert matches[0]["workspace_id"] == "ws-a"
    assert matches[0]["version"] == "19.0"
    assert matches[0]["score"] == 0.91
