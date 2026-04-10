"""HTTP fetcher: download documentation from the web into docs_center/technologies/."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ingestion.fetcher import SourceSnapshot, should_fetch
from ingestion.source_mapper import CanonicalSourceMapper, LibraryMapping
from ingestion.splitter import split_monolith

logger = logging.getLogger(__name__)

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

TEXT_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".json"}

# Signatures that indicate the fetched content is an error page, not documentation.
_ERROR_PAGE_SIGNATURES = [
    "<html",
    "<!doctype html",
    "<!DOCTYPE html",
    "cf-browser-verification",
    "cloudflare",
    "challenge-platform",
    "Access Denied",
    "<title>404",
    "<title>403",
    "<title>Just a moment",
    "Attention Required",
    "ray ID",
]


def _validate_fetched_content(content: str, url: str) -> None:
    """Reject content that looks like an error page instead of documentation.

    Raises RuntimeError if content appears to be HTML error page, Cloudflare
    challenge, or other non-documentation response.
    """
    # Check first 2000 chars for error signatures
    sample = content[:2000]

    # If it starts with HTML tags and has error signatures, reject
    stripped = sample.lstrip()
    looks_like_html = stripped.startswith(("<!", "<html", "<HTML"))

    if looks_like_html:
        sample_lower = sample.lower()
        for sig in _ERROR_PAGE_SIGNATURES:
            if sig.lower() in sample_lower:
                raise RuntimeError(
                    f"Content from {url} appears to be an HTML error page "
                    f"(matched: '{sig}'), not documentation. "
                    f"First 200 chars: {content[:200]!r}"
                )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class HttpDocFetcher:
    """Fetches documentation from the web using conditional HTTP (ETag / If-Modified-Since).

    Content is written to ``base_dir/docs_center/technologies/<tech>/`` so that the
    existing ``DocsHubService.scan()`` picks it up automatically.

    Fetch state (ETag, Last-Modified) is persisted in the SQLite database so repeated
    runs skip unchanged sources.
    """

    def __init__(
        self,
        base_dir: Path | str,
        db_path: Path | str,
        registry: list[LibraryMapping],
    ) -> None:
        self.base_dir = Path(base_dir)
        self.db_path = Path(db_path)
        self.registry = registry
        self._by_id: dict[str, LibraryMapping] = {m.library_id: m for m in registry}
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, technology: str) -> dict[str, Any]:
        """Fetch documentation for a single technology.

        Tries each source URL in priority order (llms-full.txt first, then
        llms.txt, then others).  Moves to the next URL on 404/410 or
        connection failure.  Only raises if ALL sources fail.

        If the technology is not in the registry, attempts auto-discovery via
        web search to find ``llms.txt`` endpoints.  Discovered sources are
        persisted to the registry for future fetches.

        Returns a dict with at minimum ``{"fetched": bool, "technology": str}``.

        Raises:
            ValueError: if the technology cannot be found in the registry
                        and auto-discovery fails.
            RuntimeError: on HTTP errors or connection failures for ALL sources.
        """
        mapping = self._by_id.get(technology)
        if mapping is None:
            mapping = self._try_discover(technology)
            if mapping is None:
                raise ValueError(
                    f"unknown technology '{technology}' — not in registry "
                    "and auto-discovery found no documentation sources"
                )

        ordered_sources = CanonicalSourceMapper.ordered_sources(mapping.sources)
        if not ordered_sources:
            raise ValueError(f"no sources configured for technology '{technology}'")

        previous = self._load_fetch_state(technology)
        etag = previous.etag if previous else None
        last_modified = previous.last_modified if previous else None

        errors: list[str] = []
        for url in ordered_sources:
            # GitHub docs tree sources use a different fetch path
            if self._is_github_source(url):
                try:
                    return self._fetch_github_docs(technology, url, mapping)
                except Exception as exc:
                    logger.info("GitHub source %s failed: %s", url, exc)
                    errors.append(f"{url}: {exc}")
                    continue

            try:
                return self._do_fetch(technology, url, mapping, etag, last_modified)
            except RuntimeError as exc:
                msg = str(exc)
                # Retry next source on 404/410/5xx, but not on other errors
                if "HTTP 404" in msg or "HTTP 410" in msg or "HTTP 5" in msg:
                    logger.info("Source %s returned error, trying next: %s", url, msg)
                    errors.append(f"{url}: {msg}")
                    continue
                raise
            except Exception as exc:
                logger.info("Source %s failed, trying next: %s", url, exc)
                errors.append(f"{url}: {exc}")
                continue

        raise RuntimeError(
            f"All sources failed for '{technology}':\n" + "\n".join(errors)
        )

    def fetch_all(self) -> list[dict[str, Any]]:
        """Fetch documentation for all registered technologies.

        Returns one result dict per technology. A failure on one technology is
        recorded as ``{"fetched": False, "error": True, "message": "...", "technology": "..."}``
        and does not abort the remaining fetches.
        """
        results: list[dict[str, Any]] = []
        for mapping in self.registry:
            try:
                results.append(self.fetch(mapping.library_id))
            except Exception as exc:
                results.append(
                    {
                        "fetched": False,
                        "error": True,
                        "technology": mapping.library_id,
                        "message": str(exc),
                    }
                )
        return results

    # ------------------------------------------------------------------
    # Auto-discovery for unknown technologies
    # ------------------------------------------------------------------

    def _try_discover(self, technology: str) -> LibraryMapping | None:
        """Attempt to discover documentation sources via web search.

        On success, registers the technology in the registry and local cache.
        Returns the new LibraryMapping or None.
        """
        try:
            from ingestion.doc_discovery import discover_and_register
            from ingestion.registry_loader import default_registry_path
        except ImportError:
            return None

        result = discover_and_register(
            technology=technology,
            registry_path=default_registry_path(),
            package_names=[technology],
        )

        if not result.get("discovered"):
            return None

        mapping = LibraryMapping(
            library_id=technology,
            package_names=[technology],
            sources=result["sources"],
        )
        self._by_id[technology] = mapping
        self.registry.append(mapping)
        return mapping

    # ------------------------------------------------------------------
    # Internal fetch logic
    # ------------------------------------------------------------------

    def _do_fetch(
        self,
        technology: str,
        url: str,
        mapping: LibraryMapping,
        etag: str | None,
        last_modified: str | None,
    ) -> dict[str, Any]:
        if _requests is None:  # pragma: no cover
            raise RuntimeError(
                "The 'requests' library is required for web fetching. "
                "Install it with: pip install 'buonaiuto-doc4llm[fetch]'"
            )

        headers: dict[str, str] = {
            "Accept-Language": "en-US,en;q=0.9",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        try:
            resp = _requests.get(url, headers=headers, timeout=30)
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to {url}: {exc}") from exc

        if resp.status_code == 304:
            return {"fetched": False, "reason": "not_modified", "technology": technology}

        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code} fetching {url}: {resp.text[:200]}"
            )

        content = resp.text
        _validate_fetched_content(content, url)
        dest = self._write_content(technology, url, content)
        self._write_manifest(technology, mapping)

        # Split monolith llms.txt / llms-full.txt files at h1 boundaries
        # so each section becomes individually searchable.
        if dest.name in ("llms.txt", "llms-full.txt"):
            split_dir = dest.parent / "docs"
            split_files = split_monolith(dest, split_dir)
            if split_files:
                logger.info(
                    "Split %s into %d section files in %s",
                    dest.name, len(split_files), split_dir,
                )

        # Parse llms.txt for links to individual doc pages and download them.
        linked_pages = _extract_doc_links(content, url)
        pages_fetched = 0
        pages_failed = 0
        total_page_bytes = 0
        if linked_pages:
            pages_fetched, pages_failed, total_page_bytes = self._fetch_linked_pages(
                technology, linked_pages,
            )

        # Fetch source code files referenced by templates (e.g. FastAPI {* path *})
        code_fetched = self._fetch_template_sources(technology, mapping)

        new_etag = resp.headers.get("ETag")
        new_last_modified = resp.headers.get("Last-Modified")
        self._save_fetch_state(
            technology=technology,
            url=url,
            etag=new_etag,
            last_modified=new_last_modified,
            status_code=resp.status_code,
            bytes_received=len(resp.content) + total_page_bytes,
        )

        return {
            "fetched": True,
            "technology": technology,
            "url": url,
            "dest": str(dest),
            "bytes": len(resp.content),
            "pages_fetched": pages_fetched,
            "pages_failed": pages_failed,
            "total_page_bytes": total_page_bytes,
            "code_files_fetched": code_fetched,
        }

    def _write_content(self, technology: str, url: str, content: str) -> Path:
        tech_dir = self.base_dir / "docs_center" / "technologies" / technology
        tech_dir.mkdir(parents=True, exist_ok=True)

        # Derive filename from URL path; default to llms-full.txt for llms.txt sources
        url_path = url.rstrip("/").rsplit("/", 1)[-1]
        suffix = Path(url_path).suffix
        if suffix not in TEXT_EXTENSIONS:
            url_path = "llms-full.txt"

        dest = tech_dir / url_path
        dest.write_text(content, encoding="utf-8")
        return dest

    def _write_manifest(self, technology: str, mapping: LibraryMapping) -> None:
        tech_dir = self.base_dir / "docs_center" / "technologies" / technology
        tech_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "display_name": mapping.library_id.replace("-", " ").title(),
        }
        # Preserve existing manifest fields if present
        manifest_path = tech_dir / "manifest.json"
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = {**manifest, **existing}
            except (json.JSONDecodeError, OSError):
                pass
        manifest["display_name"] = _display_name_for(mapping)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Linked page fetching
    # ------------------------------------------------------------------

    MAX_LINKED_PAGES = 500
    MAX_PAGE_BYTES = 5 * 1024 * 1024  # 5 MB per page
    MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB total

    def _fetch_linked_pages(
        self,
        technology: str,
        page_urls: list[str],
    ) -> tuple[int, int, int]:
        """Download individual doc pages linked from llms.txt.

        Returns (pages_fetched, pages_failed, total_bytes).
        """
        tech_dir = self.base_dir / "docs_center" / "technologies" / technology / "docs"
        tech_dir.mkdir(parents=True, exist_ok=True)

        fetched = 0
        failed = 0
        total_bytes = 0
        page_urls = page_urls[:self.MAX_LINKED_PAGES]

        for url in page_urls:
            if total_bytes >= self.MAX_TOTAL_BYTES:
                logger.warning("Total byte limit reached for %s, stopping", technology)
                break
            rel_path = _url_to_rel_path(url)
            if not rel_path:
                continue
            dest = tech_dir / rel_path
            if not dest.resolve().is_relative_to(tech_dir.resolve()):
                logger.warning("Path traversal blocked: %s -> %s", url, dest)
                continue
            try:
                resp = _requests.get(
                    url,
                    timeout=30,
                    headers={
                        "User-Agent": "buonaiuto-doc4llm/1.0 (documentation fetcher)",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                if resp.status_code != 200:
                    logger.debug("Skipping %s — HTTP %d", url, resp.status_code)
                    failed += 1
                    continue
                page_bytes = len(resp.content)
                if page_bytes > self.MAX_PAGE_BYTES:
                    logger.warning("Skipping oversized page %s (%d bytes)", url, page_bytes)
                    failed += 1
                    continue
                try:
                    _validate_fetched_content(resp.text, url)
                except RuntimeError:
                    logger.debug("Skipping error page %s", url)
                    failed += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(resp.text, encoding="utf-8")
                fetched += 1
                total_bytes += page_bytes
                # Split large linked pages at h1 boundaries
                if page_bytes > 100_000:
                    split_dir = dest.parent / dest.stem
                    split_files = split_monolith(dest, split_dir, min_size_bytes=100_000)
                    if split_files:
                        logger.info(
                            "Split linked page %s into %d files",
                            dest.name, len(split_files),
                        )
            except Exception as exc:
                logger.debug("Failed to fetch %s: %s", url, exc)
                failed += 1

        logger.info(
            "Fetched %d/%d linked pages for %s (%d bytes)",
            fetched, fetched + failed, technology, total_bytes,
        )
        return fetched, failed, total_bytes

    def _fetch_template_sources(
        self, technology: str, mapping: LibraryMapping,
    ) -> int:
        """Scan fetched docs for template references and download source files.

        Detects {* ../../path/to/file.py *} patterns, resolves them relative
        to the doc file, and downloads from GitHub if the source is not
        available locally.  Returns the number of files fetched.
        """
        try:
            from ingestion.template_resolver import extract_template_refs
        except ImportError:
            return 0

        tech_dir = self.base_dir / "docs_center" / "technologies" / technology
        docs_dir = tech_dir / "docs"
        if not docs_dir.exists():
            return 0

        # Find a GitHub source to download code examples from
        github_owner_repo: str | None = None
        github_branch = "master"
        for src in mapping.sources:
            if src.startswith("github://"):
                # github://owner/repo/branch/path
                parts = src.removeprefix("github://").split("/")
                if len(parts) >= 3:
                    github_owner_repo = f"{parts[0]}/{parts[1]}"
                    github_branch = parts[2]
                    break

        if github_owner_repo is None:
            return 0

        # Collect all unique template source paths across all doc files
        source_paths: set[str] = set()
        for doc_file in docs_dir.rglob("*"):
            if doc_file.suffix.lower() not in (".md", ".mdx", ".txt", ".rst"):
                continue
            try:
                content = doc_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for ref in extract_template_refs(content):
                resolved = (doc_file.parent / ref["path"]).resolve()
                if not resolved.exists():
                    # Compute path relative to the technology root
                    try:
                        rel = resolved.relative_to(tech_dir.resolve())
                    except ValueError:
                        # Path escapes tech_dir — compute from common parent
                        try:
                            rel = resolved.relative_to(tech_dir.parent.resolve())
                        except ValueError:
                            continue
                    source_paths.add(str(rel))

        if not source_paths:
            return 0

        fetched = 0
        for rel_path in sorted(source_paths):
            dest = tech_dir / rel_path
            if dest.exists():
                continue
            raw_url = (
                f"https://raw.githubusercontent.com/{github_owner_repo}"
                f"/{github_branch}/{rel_path}"
            )
            try:
                resp = _requests.get(
                    raw_url, timeout=15,
                    headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                if resp.status_code != 200:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(resp.text, encoding="utf-8")
                fetched += 1
            except Exception:
                continue

        if fetched:
            logger.info(
                "Fetched %d template source files for %s from %s",
                fetched, technology, github_owner_repo,
            )
        return fetched

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fetch_state (
                    technology       TEXT    NOT NULL PRIMARY KEY,
                    source_url       TEXT    NOT NULL,
                    etag             TEXT,
                    last_modified    TEXT,
                    last_fetched_at  TEXT    NOT NULL,
                    last_status_code INTEGER NOT NULL DEFAULT 200,
                    bytes_received   INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_fetch_state(self, technology: str) -> SourceSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT etag, last_modified FROM fetch_state WHERE technology = ?",
                (technology,),
            ).fetchone()
        if row is None:
            return None
        return SourceSnapshot(
            etag=row["etag"],
            last_modified=row["last_modified"],
            chunk_hashes=set(),
        )

    def _save_fetch_state(
        self,
        technology: str,
        url: str,
        etag: str | None,
        last_modified: str | None,
        status_code: int,
        bytes_received: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fetch_state
                    (technology, source_url, etag, last_modified, last_fetched_at, last_status_code, bytes_received)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(technology) DO UPDATE SET
                    source_url       = excluded.source_url,
                    etag             = excluded.etag,
                    last_modified    = excluded.last_modified,
                    last_fetched_at  = excluded.last_fetched_at,
                    last_status_code = excluded.last_status_code,
                    bytes_received   = excluded.bytes_received
                """,
                (technology, url, etag, last_modified, _utc_now(), status_code, bytes_received),
            )


    # ------------------------------------------------------------------
    # GitHub docs tree fetching
    # ------------------------------------------------------------------

    def _is_github_source(self, url: str) -> bool:
        """Check if a source URL points to a GitHub docs tree."""
        return url.startswith("github://") or (
            "api.github.com" in url and "/git/trees/" in url
        )

    def _fetch_github_docs(
        self, technology: str, url: str, mapping: LibraryMapping
    ) -> dict[str, Any]:
        """Fetch documentation from a GitHub repository docs tree.

        Supports two URL formats:
        - github://owner/repo/branch/path  (convenience format)
        - https://api.github.com/repos/owner/repo/git/trees/branch?recursive=1

        Downloads all .md/.mdx/.rst files from the specified path.
        """
        if _requests is None:  # pragma: no cover
            raise RuntimeError(
                "The 'requests' library is required for web fetching. "
                "Install it with: pip install 'buonaiuto-doc4llm[fetch]'"
            )

        owner, repo, branch, doc_path = _parse_github_source(url)
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"

        headers = {
            "User-Agent": "buonaiuto-doc4llm/1.0 (documentation fetcher)",
            "Accept": "application/vnd.github.v3+json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            resp = _requests.get(api_url, headers=headers, timeout=30)
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to GitHub API: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code} fetching GitHub tree for {technology}: {resp.text[:200]}"
            )

        tree_data = resp.json()
        doc_extensions = {".md", ".mdx", ".rst", ".txt"}
        files_to_fetch = []

        for item in tree_data.get("tree", []):
            if item["type"] != "blob":
                continue
            path = item["path"]
            if not path.startswith(doc_path):
                continue
            if not any(path.lower().endswith(ext) for ext in doc_extensions):
                continue
            files_to_fetch.append(path)

        tech_dir = self.base_dir / "docs_center" / "technologies" / technology / "docs"
        tech_dir.mkdir(parents=True, exist_ok=True)

        fetched = 0
        failed = 0
        total_bytes = 0

        for file_path in files_to_fetch:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
            rel_path = file_path[len(doc_path):].lstrip("/")
            if not rel_path:
                continue
            dest = tech_dir / rel_path
            if not dest.resolve().is_relative_to(tech_dir.resolve()):
                logger.warning("Path traversal blocked: %s -> %s", raw_url, dest)
                continue
            try:
                file_resp = _requests.get(raw_url, timeout=30, headers={
                    "User-Agent": "buonaiuto-doc4llm/1.0 (documentation fetcher)",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                if file_resp.status_code != 200:
                    failed += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(file_resp.text, encoding="utf-8")
                fetched += 1
                total_bytes += len(file_resp.content)
            except Exception as exc:
                logger.debug("Failed to fetch %s: %s", raw_url, exc)
                failed += 1

        self._write_manifest(technology, mapping)
        self._save_fetch_state(
            technology=technology,
            url=url,
            etag=None,
            last_modified=None,
            status_code=200,
            bytes_received=total_bytes,
        )

        logger.info(
            "Fetched %d/%d GitHub docs for %s (%d bytes)",
            fetched, fetched + failed, technology, total_bytes,
        )
        return {
            "fetched": True,
            "technology": technology,
            "url": url,
            "dest": str(tech_dir),
            "bytes": 0,
            "pages_fetched": fetched,
            "pages_failed": failed,
            "total_page_bytes": total_bytes,
        }


def _parse_github_source(url: str) -> tuple[str, str, str, str]:
    """Parse a GitHub source URL into (owner, repo, branch, path).

    Supports:
      - github://owner/repo/branch/docs/path
      - https://api.github.com/repos/owner/repo/git/trees/branch
    """
    if url.startswith("github://"):
        parts = url[len("github://"):].split("/", 3)
        owner = parts[0]
        repo = parts[1]
        branch = parts[2] if len(parts) > 2 else "main"
        path = parts[3] if len(parts) > 3 else ""
        return owner, repo, branch, path
    if "api.github.com" in url and "/git/trees/" in url:
        # https://api.github.com/repos/OWNER/REPO/git/trees/BRANCH
        import re
        m = re.search(r"/repos/([^/]+)/([^/]+)/git/trees/([^?]+)", url)
        if m:
            return m.group(1), m.group(2), m.group(3), ""
    raise ValueError(f"Unsupported GitHub source URL format: {url}")


def _display_name_for(mapping: LibraryMapping) -> str:
    # Use a friendly display name derived from the library_id
    # (the registry.json does not expose display_name through LibraryMapping yet)
    return mapping.library_id.replace("-", " ").title()


# Regex to match markdown links: [title](url)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")

# File extensions that are documentation pages worth downloading.
_DOC_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}


def _extract_doc_links(content: str, source_url: str) -> list[str]:
    """Parse llms.txt content and extract URLs to individual doc pages.

    Only returns URLs that:
    - Are absolute HTTP(S) URLs
    - Point to text/markdown file extensions
    - Share the same domain as the source URL (to avoid external links)

    Args:
        content: The text content of llms.txt.
        source_url: The URL that llms.txt was fetched from (for domain filtering).

    Returns:
        Deduplicated list of doc page URLs.
    """
    source_domain = urlsplit(source_url).hostname or ""

    seen: set[str] = set()
    urls: list[str] = []

    for _title, url in _MD_LINK_RE.findall(content):
        url = url.strip()
        if url in seen:
            continue

        parts = urlsplit(url)
        domain = parts.hostname or ""

        # Only follow links on the same domain
        if domain != source_domain and not domain.endswith("." + source_domain):
            continue

        # Only fetch doc-like extensions
        path_lower = parts.path.lower()
        if not any(path_lower.endswith(ext) for ext in _DOC_EXTENSIONS):
            continue

        seen.add(url)
        urls.append(url)

    return urls


def _url_to_rel_path(url: str) -> str | None:
    """Convert a doc page URL to a relative file path for local storage.

    Example: https://react.dev/learn/hooks.md → learn/hooks.md
    """
    from urllib.parse import unquote
    parts = urlsplit(url)
    # URL-decode first to catch %2e%2e traversal attempts
    path = unquote(parts.path).lstrip("/")
    if not path:
        return None
    # Sanitize: no parent traversal (check decoded path)
    if ".." in path:
        return None
    # Reject absolute paths
    if path.startswith("/"):
        return None
    return path
