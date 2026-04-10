from retrieval.embedder import Embedder
from retrieval.model_provider import ModelProviderRouter


class _BadProvider:
    name = "bad"

    def is_available(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Returns fewer vectors than chunks, simulating backend inconsistency.
        return [[0.1, 0.2, 0.3]]


def test_embedder_handles_provider_vector_count_mismatch_gracefully() -> None:
    embedder = Embedder(ModelProviderRouter([_BadProvider()]))
    payloads = embedder.embed_chunks(["chunk-a", "chunk-b"])

    assert len(payloads) == 2
    assert payloads[0]["embedding_status"] == "ready"
    assert payloads[0]["vector"] is not None
    assert payloads[1]["embedding_status"] == "pending"
    assert payloads[1]["vector"] is None
