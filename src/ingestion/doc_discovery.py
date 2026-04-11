"""Auto-discover documentation URLs for unknown technologies via web search.

When a technology is detected in a project but has no entry in the static
registry, this module searches for its official docs site and probes for
``llms-full.txt`` / ``llms.txt`` endpoints.  If found, the discovered source
is persisted to the registry so future fetches skip the search.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Well-known llms.txt paths to probe, in preference order.
_LLMS_PATHS = ("/llms-full.txt", "/llms.txt")

# Domain blocklist — these never host official library docs.
_BLOCKED_DOMAINS = frozenset({
    "github.com",
    "stackoverflow.com",
    "reddit.com",
    "medium.com",
    "dev.to",
    "youtube.com",
    "twitter.com",
    "x.com",
    "npmjs.com",
    "pypi.org",
    "wikipedia.org",
})


def discover_doc_sources(
    technology: str,
    search_fn: Any = None,
    timeout: int = 15,
) -> list[str]:
    """Search the web for documentation URLs for *technology*.

    Args:
        technology: Library/framework name (e.g. "dotnet", "django").
        search_fn: Callable that takes a query string and returns a list of
                   result dicts with at least a ``"url"`` key.  Defaults to
                   a simple Google Custom Search via ``requests``.
        timeout: HTTP timeout in seconds for probing candidate URLs.

    Returns:
        A list of validated documentation URLs (best first), or empty list.
    """
    if _requests is None:
        raise RuntimeError(
            "The 'requests' library is required for doc discovery. "
            "Install with: pip install requests"
        )

    candidate_domains = _search_for_docs_site(technology, search_fn)
    if not candidate_domains:
        return []

    return _probe_llms_txt(candidate_domains, timeout=timeout)


def discover_and_register(
    technology: str,
    registry_path: Path,
    package_names: list[str] | None = None,
    search_fn: Any = None,
    timeout: int = 15,
) -> dict[str, Any]:
    """Discover documentation sources and add them to the registry.

    Args:
        technology: Library/framework name.
        registry_path: Path to the registry.json file.
        package_names: Package names that map to this technology.
        search_fn: Optional custom search function.
        timeout: HTTP probe timeout.

    Returns:
        A summary dict with ``discovered``, ``sources``, and ``registered`` keys.
    """
    sources = discover_doc_sources(technology, search_fn=search_fn, timeout=timeout)
    if not sources:
        return {
            "discovered": False,
            "technology": technology,
            "sources": [],
            "registered": False,
        }

    registered = _add_to_registry(
        technology=technology,
        sources=sources,
        package_names=package_names or [technology],
        registry_path=registry_path,
    )

    return {
        "discovered": True,
        "technology": technology,
        "sources": sources,
        "registered": registered,
    }


# ------------------------------------------------------------------
# Internal: web search
# ------------------------------------------------------------------

def _search_for_docs_site(
    technology: str,
    search_fn: Any = None,
) -> list[str]:
    """Return candidate base URLs (scheme + domain) for the technology's docs.

    Uses Google search to find the official documentation site.
    """
    queries = [
        f"{technology} official documentation llms.txt",
        f"{technology} documentation site",
    ]

    seen_domains: set[str] = set()
    candidate_urls: list[str] = []

    for i, query in enumerate(queries):
        # Brief delay between Google scraping requests to reduce rate-limit risk.
        if i > 0 and search_fn is None:
            import time
            time.sleep(1)
        results = _do_search(query, search_fn)
        for url in results:
            domain = _extract_domain(url)
            if not domain or domain in _BLOCKED_DOMAINS:
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            base = _to_base_url(url)
            if base:
                candidate_urls.append(base)

    return candidate_urls


def _do_search(query: str, search_fn: Any = None) -> list[str]:
    """Execute a web search and return a list of URLs."""
    if search_fn is not None:
        results = search_fn(query)
        return [r["url"] if isinstance(r, dict) else str(r) for r in results]

    return _google_search(query)


def _google_search(query: str) -> list[str]:
    """Search Google via scraping the HTML results page.

    This is a best-effort approach that works without API keys.
    Returns up to 10 result URLs.
    """
    try:
        resp = _requests.get(
            "https://www.google.com/search",
            params={"q": query, "num": 10},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Google search failed for '%s': %s", query, exc)
        return []

    urls = _parse_google_results(resp.text)
    if not urls and len(resp.text) > 1000:
        logger.warning(
            "Google returned HTML but no parseable results for '%s' — "
            "likely rate-limited or CAPTCHA. This is NOT the same as 'technology not found'. "
            "Consider setting GOOGLE_API_KEY / GOOGLE_SEARCH_ENGINE_ID env vars for "
            "reliable search, or retry later.",
            query,
        )
    return urls


def _parse_google_results(html: str) -> list[str]:
    """Extract URLs from Google search result HTML."""
    urls: list[str] = []
    # Google wraps results in <a href="/url?q=ACTUAL_URL&...">
    for match in re.finditer(r'/url\?q=(https?://[^&"]+)', html):
        url = match.group(1)
        domain = _extract_domain(url)
        if domain and domain not in _BLOCKED_DOMAINS:
            urls.append(url)
    return urls


# ------------------------------------------------------------------
# Internal: probe llms.txt
# ------------------------------------------------------------------

def _probe_llms_txt(candidate_base_urls: list[str], timeout: int = 15) -> list[str]:
    """Probe candidate domains for llms-full.txt / llms.txt endpoints.

    Returns validated URLs that return 200 with text content.
    """
    found: list[str] = []

    headers = {"User-Agent": "buonaiuto-doc4llm/1.0 (documentation fetcher)"}
    for base_url in candidate_base_urls:
        for path in _LLMS_PATHS:
            probe_url = base_url.rstrip("/") + path
            try:
                # Try HEAD first, fall back to GET if HEAD returns 404/405
                resp = _requests.head(
                    probe_url, timeout=timeout,
                    allow_redirects=True, headers=headers,
                )
                # Fall back to GET only when HEAD is not supported (405) or
                # returns 404 — not on 403 (Forbidden), which means the
                # resource exists but access is denied.
                if resp.status_code in (404, 405):
                    resp = _requests.get(
                        probe_url, timeout=timeout,
                        allow_redirects=True, headers=headers,
                        stream=True,
                    )
                    resp.close()
                if resp.status_code == 200:
                    content_type = resp.headers.get("Content-Type", "")
                    # Reject HTML responses — they are likely error pages or SPAs,
                    # not plain-text documentation files.
                    if "html" in content_type:
                        logger.debug(
                            "Skipping %s — Content-Type is HTML (%s)",
                            probe_url, content_type,
                        )
                        continue
                    # Accept plain text types; require a small GET to validate
                    # octet-stream responses are actually text (not binary blobs).
                    if "text" in content_type:
                        found.append(probe_url)
                        logger.info("Discovered %s for docs", probe_url)
                    elif "octet-stream" in content_type:
                        # Probe a small GET to confirm the payload is text
                        try:
                            probe_resp = _requests.get(
                                probe_url, timeout=timeout,
                                headers=headers, stream=True,
                            )
                            first_chunk = next(probe_resp.iter_content(512), b"")
                            probe_resp.close()
                            if first_chunk and b"\x00" not in first_chunk:
                                found.append(probe_url)
                                logger.info("Discovered %s for docs (octet-stream validated)", probe_url)
                        except Exception:
                            pass
            except Exception:
                continue

    return found


# ------------------------------------------------------------------
# Internal: registry persistence
# ------------------------------------------------------------------

def _add_to_registry(
    technology: str,
    sources: list[str],
    package_names: list[str],
    registry_path: Path,
) -> bool:
    """Append a new library entry to registry.json.

    Returns True if the entry was added, False if already present.
    """
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    libraries = data.get("libraries", [])

    # Don't add duplicates
    existing_ids = {lib.get("library_id") for lib in libraries}
    if technology in existing_ids:
        return False

    libraries.append({
        "library_id": technology,
        "display_name": technology.replace("-", " ").title(),
        "package_names": package_names,
        "sources": sources,
    })

    data["libraries"] = libraries
    registry_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


# ------------------------------------------------------------------
# URL helpers
# ------------------------------------------------------------------

def _extract_domain(url: str) -> str | None:
    """Return the bare domain (no www.) from a URL."""
    try:
        parts = urlsplit(url)
        domain = parts.hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain if domain else None
    except Exception:
        return None


def _to_base_url(url: str) -> str | None:
    """Strip a URL to scheme + domain (e.g. https://react.dev)."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    except Exception:
        return None
