from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSnapshot:
    etag: str | None
    last_modified: str | None
    chunk_hashes: set[str]  # Currently unused — not populated from DB. Reserved for future chunk-level diffing.


def should_fetch(previous: SourceSnapshot | None, etag: str | None, last_modified: str | None) -> bool:
    """Evaluate HTTP-level metadata idempotency."""
    if previous is None:
        return True

    if etag is not None and previous.etag == etag:
        return False
    if last_modified is not None and previous.last_modified == last_modified:
        return False

    if etag is not None and previous.etag is not None and previous.etag != etag:
        return True
    if last_modified is not None and previous.last_modified is not None and previous.last_modified != last_modified:
        return True

    return True


def compute_changed_chunk_hashes(previous_hashes: set[str], current_chunks: list[str]) -> tuple[set[str], set[str]]:
    """Two-stage idempotency helper: hash chunks and return changed subset."""
    current_hashes = {_sha256_text(chunk) for chunk in current_chunks}
    changed_hashes = current_hashes - previous_hashes
    return current_hashes, changed_hashes


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

