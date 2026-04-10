from unittest.mock import MagicMock, patch

from retrieval.model_provider import ModelProviderRouter, OllamaEmbeddingProvider


def test_router_selects_ollama_provider_when_available() -> None:
    provider = OllamaEmbeddingProvider(name="ollama", model="nomic-embed-text")
    router = ModelProviderRouter([provider])

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
    mock_resp.text = '{"embedding": [0.1, 0.2, 0.3]}'

    with patch("requests.post", return_value=mock_resp):
        result = router.embed_texts(["hello world"], preferred="ollama")

    assert result["provider"] == "ollama"
    assert result["embedding_status"] == "ready"
    assert len(result["vectors"]) == 1
