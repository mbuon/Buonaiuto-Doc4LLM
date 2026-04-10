"""Tests for SentenceTransformersEmbeddingProvider."""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest

from retrieval.sentence_transformers_provider import SentenceTransformersEmbeddingProvider, _MODEL_CACHE


class TestSentenceTransformersProvider:
    def test_is_available_false_when_library_missing(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="st", model_name="all-MiniLM-L6-v2")
        with patch("importlib.util.find_spec", return_value=None):
            assert provider.is_available() is False

    def test_is_available_true_when_library_present(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="st", model_name="all-MiniLM-L6-v2")
        fake_spec = MagicMock()
        with patch("importlib.util.find_spec", return_value=fake_spec):
            assert provider.is_available() is True

    def test_embed_returns_list_of_float_lists(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="st", model_name="all-MiniLM-L6-v2")
        import numpy as np

        fake_model = MagicMock()
        fake_model.encode.return_value = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

        fake_spec = MagicMock()
        fake_st_module = MagicMock()
        fake_st_module.SentenceTransformer.return_value = fake_model

        # Clear model cache so the mock model is used instead of a cached real model
        _MODEL_CACHE.pop("all-MiniLM-L6-v2", None)

        with patch("importlib.util.find_spec", return_value=fake_spec):
            with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
                result = provider.embed(["text one", "text two"])

        assert len(result) == 2
        assert result[0] == pytest.approx([0.1, 0.2, 0.3])
        assert result[1] == pytest.approx([0.4, 0.5, 0.6])
        assert all(isinstance(v, float) for v in result[0])

    def test_embed_raises_when_unavailable(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="st", model_name="all-MiniLM-L6-v2")
        with patch("importlib.util.find_spec", return_value=None):
            with pytest.raises(RuntimeError, match="sentence-transformers"):
                provider.embed(["hello"])

    def test_name_field_stored_correctly(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="my-st", model_name="all-MiniLM-L6-v2")
        assert provider.name == "my-st"

    def test_model_name_default(self) -> None:
        provider = SentenceTransformersEmbeddingProvider(name="st")
        assert provider.model_name == "all-MiniLM-L6-v2"
