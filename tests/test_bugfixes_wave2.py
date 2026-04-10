from pathlib import Path

import pytest

from api.admin import TrustAdminService
from api.app import ApiService
from api.mcp_http import HostedMCPGateway
from api.webhooks import StripeWebhookHandler
from control.api_keys import ApiKeyService
from control.billing import BillingService
from control.quotas import QuotaLimiter
from buonaiuto_doc4llm.service import DocsHubService
from ingestion.private_connector import PrivateRepoConnector
from ingestion.scheduler import IngestionScheduler, IngestionTrigger
from ingestion.source_mapper import CanonicalSourceMapper, LibraryMapping
from retrieval.qdrant_client import QdrantHybridClient, QdrantQuery


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
        "# Hooks\n\nHooks let you use state.",
    )
    service = DocsHubService(tmp_path)
    service.scan()
    return service


def test_rate_limit_rejection_does_not_consume_daily_quota(tmp_path: Path) -> None:
    docs_service = _service(tmp_path)
    auth = lambda api_key: "ws-a" if api_key == "good" else None
    gateway = HostedMCPGateway(service=docs_service, authenticate=auth)
    stripe = StripeWebhookHandler(verifier=lambda payload, sig: {}, billing_service=BillingService())
    api = ApiService(gateway=gateway, stripe_handler=stripe, quotas=QuotaLimiter(), authenticate=auth)

    with pytest.raises(PermissionError, match="Rate limit exceeded"):
        api.handle_query(
            api_key="good",
            query_text="hooks",
            libraries=[{"id": "react", "version": "19.0"}],
            date_key="2026-03-19",
            minute_key="2026-03-19T10:00",
            daily_limit=1,
            rpm_limit=0,
            stream=False,
        )

    payload = api.handle_query(
        api_key="good",
        query_text="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        date_key="2026-03-19",
        minute_key="2026-03-19T10:01",
        daily_limit=1,
        rpm_limit=10,
        stream=False,
    )
    assert payload["results"]


def test_search_documentation_result_includes_numeric_score(tmp_path: Path) -> None:
    service = _service(tmp_path)

    payload = service.search_documentation(
        query="hooks",
        libraries=[{"id": "react", "version": "19.0"}],
        workspace_id="ws-a",
    )

    assert payload["results"]
    assert "score" in payload["results"][0]
    assert payload["results"][0]["score"] > 0


class _DictResponseBackend:
    def __init__(self) -> None:
        self.calls = 0

    def query_points(self, **kwargs):
        self.calls += 1
        return {
            "result": [
                {
                    "payload": {
                        "workspace_id": "ws-a",
                        "library_id": "react",
                        "version": "19.0",
                        "rel_path": "docs/hooks.md",
                        "title": "Hooks",
                        "source_uri": "doc://react/docs/hooks.md",
                        "snippet": "Use hooks",
                    },
                    "score": 0.77,
                }
            ]
        }


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_qdrant_client_extracts_points_from_dict_response() -> None:
    backend = _DictResponseBackend()
    client = QdrantHybridClient(client=backend, collection_name="docs", embedder=_FakeEmbedder())

    matches = client.query_hybrid(
        QdrantQuery(
            workspace_id="ws-a",
            library_id="react",
            version="19.0",
            query_text="hooks",
            limit=5,
        )
    )

    assert len(matches) == 1
    assert matches[0]["score"] == 0.77


def test_qdrant_client_skips_backend_for_blank_query_or_nonpositive_limit() -> None:
    backend = _DictResponseBackend()
    client = QdrantHybridClient(client=backend, collection_name="docs")

    assert (
        client.query_hybrid(
            QdrantQuery(
                workspace_id="ws-a",
                library_id="react",
                version="19.0",
                query_text="   ",
                limit=5,
            )
        )
        == []
    )
    assert (
        client.query_hybrid(
            QdrantQuery(
                workspace_id="ws-a",
                library_id="react",
                version="19.0",
                query_text="hooks",
                limit=0,
            )
        )
        == []
    )
    assert backend.calls == 0


def test_scheduler_normalizes_event_key_for_deduplication() -> None:
    scheduler = IngestionScheduler()
    first = scheduler.enqueue(
        IngestionTrigger(
            source_type="pypi",
            library_id="fastapi",
            version="0.1",
            event_key="  pypi:fastapi:0.1  ",
        )
    )
    second = scheduler.enqueue(
        IngestionTrigger(
            source_type="pypi",
            library_id="fastapi",
            version="0.1",
            event_key="pypi:fastapi:0.1",
        )
    )

    assert first is True
    assert second is False
    assert len(scheduler.pending()) == 1


def test_private_connector_validates_repo_and_rel_path() -> None:
    connector = PrivateRepoConnector()

    with pytest.raises(ValueError, match="repo is required"):
        connector.build_ingestion_record(repo="   ", workspace_id="ws-a", rel_path="README.md")

    with pytest.raises(ValueError, match="rel_path is required"):
        connector.build_ingestion_record(repo="git@example.com/repo.git", workspace_id="ws-a", rel_path="   ")


def test_source_mapper_normalizes_package_and_prefers_llms_urls_robustly() -> None:
    mapper = CanonicalSourceMapper(
        [
            LibraryMapping(
                library_id="nextjs",
                package_names=["next"],
                sources=["https://nextjs.org/docs"],
            )
        ]
    )

    resolved = mapper.resolve_by_package("  Next  ")
    assert resolved is not None
    assert resolved.library_id == "nextjs"

    preferred = mapper.preferred_source(
        [
            "https://example.com/docs",
            "https://example.com/LLMS.TXT?download=1",
        ]
    )
    assert preferred == "https://example.com/LLMS.TXT?download=1"


def test_trust_admin_validates_missing_chunk_and_empty_reviewer() -> None:
    admin = TrustAdminService()

    with pytest.raises(ValueError, match="Unknown chunk_id"):
        admin.mark_reviewed("missing", reviewer="ops")

    admin.submit_for_review("chunk-1", "ws-a", "reason")
    with pytest.raises(ValueError, match="reviewer is required"):
        admin.mark_reviewed("chunk-1", reviewer="   ")


def test_api_key_service_rejects_unsafe_prefix_and_malformed_key_id() -> None:
    service = ApiKeyService(secret="test-secret")

    with pytest.raises(ValueError, match="must not contain '_' characters"):
        service.generate_key(prefix="wk_prod")

    with pytest.raises(ValueError, match="Malformed API key"):
        service.key_id("wk__token")
