from retrieval.model_provider import (
    DeterministicLocalEmbeddingProvider,
    DisabledEmbeddingProvider,
    ModelProviderRouter,
)


def test_router_returns_pending_when_no_provider_is_available() -> None:
    router = ModelProviderRouter([DisabledEmbeddingProvider(name="cohere")])
    result = router.embed_texts(["alpha", "beta"])

    assert result["embedding_status"] == "pending"
    assert result["provider"] is None
    assert result["vectors"] == []


def test_router_uses_available_provider_and_returns_vectors() -> None:
    router = ModelProviderRouter([DeterministicLocalEmbeddingProvider(name="bge-m3-local")])
    result = router.embed_texts(["alpha", "beta"])

    from retrieval.model_provider import _DETERMINISTIC_DIM
    assert result["embedding_status"] == "ready"
    assert result["provider"] == "bge-m3-local"
    assert len(result["vectors"]) == 2
    assert all(len(vector) == _DETERMINISTIC_DIM for vector in result["vectors"])
