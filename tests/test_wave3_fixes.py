"""Tests for wave-3 fixes: flash close buttons, ST provider logging suppression,
service edge cases, and dashboard endpoint coverage."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from buonaiuto_doc4llm.dashboard import create_app
from buonaiuto_doc4llm.service import (
    DocsHubService,
    _build_toc,
    _clean_content,
    _extract_section,
    _section_title,
    _split_sections,
)
from retrieval.sentence_transformers_provider import SentenceTransformersEmbeddingProvider, _MODEL_CACHE


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _service(tmp_path: Path) -> DocsHubService:
    _write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    _write(
        tmp_path / "docs_center/technologies/react/docs/hooks.md",
        "# Hooks\n\nuseState and friends.\n\n## useEffect\n\nSide effects.\n",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    base_dir = tmp_path / "base"
    _write(
        base_dir / "docs_center/technologies/react/manifest.json",
        json.dumps({"technology": "react", "version": "19.0", "display_name": "React"}),
    )
    _write(
        base_dir / "docs_center/technologies/react/docs/hooks.md",
        "# React Hooks\n\nuseState, useEffect, useRef",
    )
    _write(
        base_dir / "docs_center/projects/myapp.json",
        json.dumps({"project_id": "myapp", "name": "My App", "technologies": ["react"]}),
    )
    app = create_app(base_dir)
    app.state.service.scan()
    app.state.service.sync_projects()
    return TestClient(app)


# ── 1-3: Flash close button present in dashboard HTML ──


def test_flash_partial_contains_close_button(client: TestClient) -> None:
    """The scan API returns flash HTML with a close button."""
    resp = client.post("/api/scan")
    assert resp.status_code == 200
    assert "flash-close" in resp.text
    assert "&times;" in resp.text or "\u00d7" in resp.text


def test_technologies_page_flash_has_close_button(client: TestClient) -> None:
    """Flash messages on the technologies page have a dismiss button."""
    resp = client.get("/technologies?flash_msg=Test+error&flash_type=error")
    assert resp.status_code == 200
    assert "flash-close" in resp.text
    assert "flash-error" in resp.text


def test_projects_page_flash_has_close_button(client: TestClient) -> None:
    """Flash messages on the projects page have a dismiss button."""
    resp = client.get("/projects?flash_msg=Done&flash_type=success")
    assert resp.status_code == 200
    assert "flash-close" in resp.text


# ── 4-5: Flash CSS styles in stylesheet ──


def test_flash_close_css_exists(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert ".flash-close" in resp.text
    assert ".flash-msg" in resp.text


def test_flash_close_css_has_hover(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert ".flash-close:hover" in resp.text


# ── 6-8: SentenceTransformersEmbeddingProvider logging suppression ──


def test_st_provider_suppresses_transformers_warnings() -> None:
    """Model loading should temporarily set transformers verbosity to ERROR."""
    import transformers.utils.logging as tf_logging

    _MODEL_CACHE.pop("test-model-suppress", None)
    provider = SentenceTransformersEmbeddingProvider(name="st", model_name="test-model-suppress")

    fake_model = MagicMock()
    fake_st_module = MagicMock()
    fake_st_module.SentenceTransformer.return_value = fake_model

    original_verbosity = tf_logging.get_verbosity()
    captured_during_load: list[int] = []

    real_st = fake_st_module.SentenceTransformer
    def capturing_constructor(*a, **kw):
        captured_during_load.append(tf_logging.get_verbosity())
        return real_st.return_value
    fake_st_module.SentenceTransformer.side_effect = capturing_constructor

    with patch("importlib.util.find_spec", return_value=MagicMock()):
        with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
            provider._get_model()

    # During loading, verbosity should have been ERROR (40)
    assert captured_during_load[0] == 40
    # After loading, verbosity should be restored
    assert tf_logging.get_verbosity() == original_verbosity
    _MODEL_CACHE.pop("test-model-suppress", None)


def test_st_provider_restores_verbosity_on_error() -> None:
    """If model loading fails, verbosity should still be restored."""
    import transformers.utils.logging as tf_logging

    _MODEL_CACHE.pop("test-model-fail", None)
    provider = SentenceTransformersEmbeddingProvider(name="st", model_name="test-model-fail")

    original_verbosity = tf_logging.get_verbosity()

    fake_st_module = MagicMock()
    fake_st_module.SentenceTransformer.side_effect = RuntimeError("bad model")

    with patch("importlib.util.find_spec", return_value=MagicMock()):
        with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
            with pytest.raises(RuntimeError, match="bad model"):
                provider._get_model()

    # Verbosity must be restored even after failure
    assert tf_logging.get_verbosity() == original_verbosity
    _MODEL_CACHE.pop("test-model-fail", None)


def test_st_provider_caches_model_across_calls() -> None:
    """Second call should use cache, not reload model."""
    _MODEL_CACHE.pop("test-model-cache", None)
    provider = SentenceTransformersEmbeddingProvider(name="st", model_name="test-model-cache")

    fake_model = MagicMock()
    fake_st_module = MagicMock()
    fake_st_module.SentenceTransformer.return_value = fake_model

    fake_tf_logging = MagicMock()
    fake_tf_logging.get_verbosity.return_value = 30

    with patch("importlib.util.find_spec", return_value=MagicMock()):
        with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
            with patch.dict("sys.modules", {"transformers.utils.logging": fake_tf_logging}):
                m1 = provider._get_model()
                m2 = provider._get_model()

    assert m1 is m2
    # SentenceTransformer constructor should only be called once
    fake_st_module.SentenceTransformer.assert_called_once()
    _MODEL_CACHE.pop("test-model-cache", None)


# ── 9-11: Service — clean_content, section extraction ──


def test_clean_content_strips_frontmatter() -> None:
    content = "---\ntitle: Test\ndate: 2025-01-01\n---\n\n# Hello\n\nWorld"
    cleaned = _clean_content(content)
    assert "---" not in cleaned
    assert "title: Test" not in cleaned
    assert "# Hello" in cleaned


def test_clean_content_strips_html_tags() -> None:
    content = '<div class="warning"><p>Do not use</p></div>\n\nSafe text.'
    cleaned = _clean_content(content)
    assert "<div" not in cleaned
    assert "<p>" not in cleaned
    assert "Do not use" in cleaned
    assert "Safe text." in cleaned


def test_extract_section_case_insensitive() -> None:
    content = "# Main\n\nIntro.\n\n## Getting Started\n\nStep one.\n\n## API\n\nDocs.\n"
    result = _extract_section(content, "getting started")
    assert result is not None
    assert "Step one" in result


def test_extract_section_returns_none_for_missing() -> None:
    content = "# Main\n\nIntro.\n\n## API\n\nDocs.\n"
    result = _extract_section(content, "nonexistent section")
    assert result is None


# ── 12-14: Service — diff_since, list_docs, read_full_page ──


def test_diff_since_returns_events_after_timestamp(tmp_path: Path) -> None:
    service = _service(tmp_path)
    # Use an old timestamp — all events should be returned
    result = service.diff_since("2000-01-01T00:00:00")
    assert result["total_count"] >= 1
    assert len(result["events"]) >= 1
    assert result["events"][0]["event_type"] == "added"


def test_diff_since_filters_by_technology(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.diff_since("2000-01-01T00:00:00", technology="react")
    assert result["total_count"] >= 1
    # Non-existent technology returns empty
    result2 = service.diff_since("2000-01-01T00:00:00", technology="vue")
    assert result2["total_count"] == 0


def test_list_docs_with_path_prefix(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs_center/technologies/react/manifest.json",
        '{"technology":"react","version":"19.0"}',
    )
    _write(tmp_path / "docs_center/technologies/react/docs/hooks.md", "# Hooks\nContent")
    _write(tmp_path / "docs_center/technologies/react/api/ref.md", "# Ref\nContent")
    _write(
        tmp_path / "docs_center/projects/app.json",
        '{"project_id":"app","name":"App","technologies":["react"]}',
    )
    service = DocsHubService(tmp_path)
    service.scan()

    all_docs = service.list_docs("react")
    assert len(all_docs) >= 2

    filtered = service.list_docs("react", path_prefix="docs/")
    assert all(d["rel_path"].startswith("docs/") for d in filtered)
    assert len(filtered) < len(all_docs)


def test_read_full_page_version_mismatch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        service.read_full_page(
            library_id="react", version="99.0", rel_path="docs/hooks.md",
        )


# ── 15-17: Service — resources, build_update_prompt ──


def test_list_resources_includes_docs_and_updates(tmp_path: Path) -> None:
    service = _service(tmp_path)
    resources = service.list_resources()
    uris = [r["uri"] for r in resources]
    assert any(u.startswith("updates://") for u in uris)
    assert any(u.startswith("doc://") for u in uris)


def test_read_resource_doc_uri(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.read_resource("doc://react/docs/hooks.md")
    assert result["mimeType"] == "text/markdown"
    assert "Hooks" in result["text"]


def test_read_resource_malformed_uri(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="Unsupported resource URI"):
        service.read_resource("ftp://bad/uri")
    with pytest.raises(ValueError, match="Malformed doc URI"):
        service.read_resource("doc://noslash")


# ── 18-20: Dashboard — query page, documents search, schedule ──


def test_query_page_loads(client: TestClient) -> None:
    resp = client.get("/query")
    assert resp.status_code == 200
    assert "react" in resp.text


def test_api_read_doc_missing_returns_error(client: TestClient) -> None:
    resp = client.get("/api/read-doc?technology=react&rel_path=nonexistent.md")
    assert resp.status_code == 200
    assert "flash-error" in resp.text or "Unknown document" in resp.text


def test_api_ack_updates_cursor(client: TestClient) -> None:
    """Acknowledging updates should advance the project cursor."""
    resp = client.post("/api/ack?project_id=myapp")
    assert resp.status_code == 200
    assert "Acknowledged" in resp.text
    # Second ack should still succeed
    resp2 = client.post("/api/ack?project_id=myapp")
    assert resp2.status_code == 200
