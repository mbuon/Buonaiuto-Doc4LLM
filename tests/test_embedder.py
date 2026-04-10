from retrieval.embedder import Embedder
from retrieval.model_provider import (
    DeterministicLocalEmbeddingProvider,
    DisabledEmbeddingProvider,
    ModelProviderRouter,
)


def test_embedder_marks_pending_when_provider_unavailable() -> None:
    embedder = Embedder(ModelProviderRouter([DisabledEmbeddingProvider(name="cohere")]))
    payloads = embedder.embed_chunks(["first chunk"])

    assert payloads[0]["embedding_status"] == "pending"
    assert payloads[0]["vector"] is None


def test_embedder_attaches_vectors_when_provider_available() -> None:
    embedder = Embedder(ModelProviderRouter([DeterministicLocalEmbeddingProvider(name="local-bge")]))
    payloads = embedder.embed_chunks(["first chunk", "second chunk"])

    assert all(payload["embedding_status"] == "ready" for payload in payloads)
    assert all(payload["vector"] is not None for payload in payloads)
