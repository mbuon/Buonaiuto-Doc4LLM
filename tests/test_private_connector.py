import pytest

from ingestion.private_connector import PrivateRepoConnector


def test_private_connector_requires_workspace_binding() -> None:
    connector = PrivateRepoConnector()
    with pytest.raises(ValueError, match="workspace_id is required"):
        connector.build_ingestion_record(repo="git@example.com:org/private.git", workspace_id="", rel_path="README.md")


def test_private_connector_attaches_workspace_to_record() -> None:
    connector = PrivateRepoConnector()
    record = connector.build_ingestion_record(
        repo="git@example.com:org/private.git",
        workspace_id="ws-a",
        rel_path="README.md",
    )

    assert record["workspace_id"] == "ws-a"
    assert record["visibility"] == "private"
    assert isinstance(record["trace_id"], str)
    assert record["trace_id"]


def test_private_connector_preserves_explicit_trace_id() -> None:
    connector = PrivateRepoConnector()
    record = connector.build_ingestion_record(
        repo="git@example.com:org/private.git",
        workspace_id="ws-a",
        rel_path="README.md",
        trace_id="trace-private-123",
    )

    assert record["trace_id"] == "trace-private-123"
