"""Tests for OllamaEmbeddingProvider with real HTTP calls (mocked via patch)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from retrieval.model_provider import OllamaEmbeddingProvider


class TestOllamaEmbedReal:
    def test_embed_returns_float_list_on_success(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3, 0.4]}
        mock_resp.text = '{"embedding": [0.1, 0.2, 0.3, 0.4]}'

        with patch("requests.post", return_value=mock_resp):
            result = provider.embed(["hello world"])

        assert len(result) == 1
        assert result[0] == pytest.approx([0.1, 0.2, 0.3, 0.4])

    def test_embed_multiple_texts_returns_one_vector_per_text(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": [0.5, 0.6]}
        mock_resp.text = '{"embedding": [0.5, 0.6]}'

        with patch("requests.post", return_value=mock_resp):
            result = provider.embed(["text one", "text two"])

        assert len(result) == 2
        assert all(isinstance(v, float) for v in result[0])

    def test_embed_raises_on_non_200_status(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "service unavailable"

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="503"):
                provider.embed(["hello"])

    def test_embed_raises_on_connection_error(self) -> None:
        import requests as req_lib
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")

        with patch("requests.post", side_effect=req_lib.ConnectionError("Connection refused")):
            with pytest.raises(RuntimeError, match="[Cc]onnect"):
                provider.embed(["hello"])

    def test_embed_raises_when_embedding_field_missing(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"not_embedding": [1, 2, 3]}
        mock_resp.text = '{"not_embedding": [1,2,3]}'

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="embedding"):
                provider.embed(["hello"])

    def test_is_available_false_when_model_empty(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="")
        assert provider.is_available() is False

    def test_is_available_true_when_model_set(self) -> None:
        provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
        assert provider.is_available() is True
