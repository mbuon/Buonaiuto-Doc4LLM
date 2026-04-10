from ingestion.fetcher import SourceSnapshot, compute_changed_chunk_hashes, should_fetch


def test_should_fetch_false_when_etag_is_unchanged() -> None:
    previous = SourceSnapshot(etag="abc123", last_modified=None, chunk_hashes={"h1"})
    assert should_fetch(previous, etag="abc123", last_modified=None) is False


def test_should_fetch_true_when_metadata_changes() -> None:
    previous = SourceSnapshot(etag="abc123", last_modified="Tue, 11 Mar 2026 10:00:00 GMT", chunk_hashes={"h1"})
    assert should_fetch(previous, etag="def456", last_modified="Wed, 12 Mar 2026 10:00:00 GMT") is True


def test_compute_changed_chunk_hashes_returns_only_deltas() -> None:
    previous = {"h1", "h2"}
    current_chunks = ["alpha", "beta", "gamma"]

    current_hashes, changed_hashes = compute_changed_chunk_hashes(previous, current_chunks)

    assert len(current_hashes) == 3
    assert changed_hashes == (current_hashes - previous)
