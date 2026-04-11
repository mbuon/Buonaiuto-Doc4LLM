from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]


class EmbeddingProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class DisabledEmbeddingProvider:
    name: str

    def is_available(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError(f"Provider {self.name} is not available.")


@dataclass(frozen=True)
class DeterministicLocalEmbeddingProvider:
    name: str

    def is_available(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_text_to_vector(text) for text in texts]


@dataclass(frozen=True)
class OllamaEmbeddingProvider:
    name: str
    model: str
    base_url: str = "http://localhost:11434"

    def is_available(self) -> bool:
        if not self.model.strip() or _requests is None:
            return False
        try:
            resp = _requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if _requests is None:
            raise RuntimeError(
                "The 'requests' library is required for Ollama embeddings. "
                "Install it with: pip install 'buonaiuto-doc4llm[embeddings-ollama]'"
            )
        results: list[list[float]] = []
        for text in texts:
            try:
                resp = _requests.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=30,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to connect to Ollama at {self.base_url}: {exc}"
                ) from exc

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Ollama /api/embeddings returned {resp.status_code}: {resp.text[:200]}"
                )
            embedding = resp.json().get("embedding")
            if not isinstance(embedding, list):
                raise RuntimeError(
                    f"Ollama response missing 'embedding' field: {resp.text[:200]}"
                )
            results.append([float(v) for v in embedding])
        return results


class ModelProviderRouter:
    def __init__(self, providers: list[EmbeddingProvider]):
        self._providers = providers

    def select_provider(self, preferred: str | None = None) -> EmbeddingProvider | None:
        if preferred is not None:
            for provider in self._providers:
                if provider.name == preferred and provider.is_available():
                    return provider
            return None
        for provider in self._providers:
            if provider.is_available():
                return provider
        return None

    def embed_texts(self, texts: list[str], preferred: str | None = None) -> dict[str, object]:
        provider = self.select_provider(preferred=preferred)
        if provider is None:
            return {"provider": None, "embedding_status": "pending", "vectors": []}

        vectors = provider.embed(texts)
        return {"provider": provider.name, "embedding_status": "ready", "vectors": vectors}


_DETERMINISTIC_DIM = 384  # Must match the Qdrant collection dimension


def _text_to_vector(text: str) -> list[float]:
    """Produce a deterministic pseudo-embedding of dimension _DETERMINISTIC_DIM.

    The original 3-element implementation was incompatible with Qdrant collections
    created with size=384.  We now tile SHA-256 digests to fill the required
    dimension, then L2-normalise the result so cosine similarity is meaningful.
    """
    import math
    result: list[float] = []
    seed = text.encode("utf-8")
    i = 0
    while len(result) < _DETERMINISTIC_DIM:
        digest = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        for offset in range(0, 32, 4):
            if len(result) >= _DETERMINISTIC_DIM:
                break
            result.append(int.from_bytes(digest[offset:offset + 4], "big") / 0xFFFFFFFF)
        i += 1
    # L2-normalise
    norm = math.sqrt(sum(v * v for v in result)) or 1.0
    return [round(v / norm, 6) for v in result]
