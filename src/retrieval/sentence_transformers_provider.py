"""SentenceTransformers embedding provider — fully offline, no Ollama required."""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field

# Module-level model cache to avoid reloading on every embed() call.
_MODEL_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class SentenceTransformersEmbeddingProvider:
    """Embedding provider backed by the sentence-transformers library.

    The library is an optional dependency. ``is_available()`` returns ``False``
    when it is not installed; ``embed()`` raises ``RuntimeError`` in that case.
    """

    name: str
    model_name: str = "all-MiniLM-L6-v2"

    def is_available(self) -> bool:
        return importlib.util.find_spec("sentence_transformers") is not None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.is_available():
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install it with: pip install 'buonaiuto-doc4llm[embeddings-st]'"
            )

        model = self._get_model()
        vectors = model.encode(texts, show_progress_bar=False)  # type: ignore[union-attr]
        return [v.tolist() for v in vectors]

    def _get_model(self) -> object:
        if self.model_name not in _MODEL_CACHE:
            from sentence_transformers import SentenceTransformer  # deferred import

            # Suppress the harmless "position_ids UNEXPECTED" load report and
            # the "Loading weights" tqdm progress bar during model init.
            import transformers.utils.logging as _tf_logging

            prev_verbosity = _tf_logging.get_verbosity()
            _tf_logging.set_verbosity_error()
            _tf_logging.disable_progress_bar()
            try:
                _MODEL_CACHE[self.model_name] = SentenceTransformer(self.model_name)
            finally:
                _tf_logging.set_verbosity(prev_verbosity)
                _tf_logging.enable_progress_bar()
        return _MODEL_CACHE[self.model_name]
