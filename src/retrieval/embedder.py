from __future__ import annotations

from retrieval.model_provider import ModelProviderRouter


class Embedder:
    def __init__(self, provider_router: ModelProviderRouter):
        self.provider_router = provider_router

    def embed_chunks(self, chunks: list[str], preferred_provider: str | None = None) -> list[dict[str, object]]:
        provider_output = self.provider_router.embed_texts(chunks, preferred=preferred_provider)
        status = str(provider_output["embedding_status"])
        vectors = provider_output["vectors"]
        if not isinstance(vectors, list):
            vectors = []

        payloads: list[dict[str, object]] = []
        for index, chunk in enumerate(chunks):
            vector = None
            item_status = status
            if status == "ready" and index < len(vectors):
                vector = vectors[index]
            elif status == "ready":
                item_status = "pending"
            payloads.append(
                {
                    "chunk_index": index,
                    "text": chunk,
                    "provider": provider_output["provider"],
                    "embedding_status": item_status,
                    "vector": vector,
                }
            )
        return payloads
