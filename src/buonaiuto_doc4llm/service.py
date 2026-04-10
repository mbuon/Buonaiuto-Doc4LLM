from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buonaiuto_doc4llm.auto_setup import bootstrap_project
from retrieval.retriever import HybridRetriever, RetrievalDocument, RetrievalQuery, RetrievalResponse
from telemetry import ensure_trace_id

# Optional: DocIndexer is only wired when Qdrant is configured
try:
    from buonaiuto_doc4llm.indexer import DocIndexer as _DocIndexer
except ImportError:  # pragma: no cover
    _DocIndexer = None  # type: ignore[assignment,misc]


TEXT_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".json"}

# Approximate chars-per-token ratio (conservative: 1 token ≈ 4 chars).
_CHARS_PER_TOKEN = 4
# Default token budget for read operations — keeps responses under MCP limits.
DEFAULT_MAX_TOKENS = 20_000


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _split_sections(content: str) -> list[str]:
    """Split markdown or RST content into sections by heading boundaries.

    Markdown: splits on ``#``-style headings (levels 1-4).
    RST: splits on underline-style headings (``===``, ``---``, ``~~~``, ``^^^``).
    Skips ``#`` characters inside fenced code blocks (``` or ~~~).
    """
    import re

    # Detect RST by checking for underline-style headings
    _RST_HEADING_RE = re.compile(
        r'^([^\n]+)\n([=\-~^#*+]{3,})\s*$', flags=re.MULTILINE,
    )
    is_rst = bool(_RST_HEADING_RE.search(content))

    if is_rst:
        # Split on RST section headings (line + underline of same length)
        parts = _RST_HEADING_RE.split(content)
        if len(parts) <= 1:
            return [content] if content.strip() else []
        # Reassemble: parts alternate [pre, title, underline, body, title, underline, body, ...]
        sections: list[str] = []
        # First part is content before the first heading
        if parts[0].strip():
            sections.append(parts[0])
        # Remaining: groups of (title, underline, body)
        for i in range(1, len(parts) - 1, 3):
            title = parts[i] if i < len(parts) else ""
            underline = parts[i + 1] if i + 1 < len(parts) else ""
            body = parts[i + 2] if i + 2 < len(parts) else ""
            section_text = f"{title}\n{underline}\n{body}"
            if section_text.strip():
                sections.append(section_text)
        return sections if sections else [content]

    # Markdown: mask fenced code blocks so their # lines aren't treated as headings
    _FENCE_RE = re.compile(r'^(`{3,}|~{3,}).*?\n(.*?)\n\1', flags=re.MULTILINE | re.DOTALL)

    masked = content
    replacements: list[tuple[str, str]] = []
    for match in _FENCE_RE.finditer(content):
        placeholder = "\x00CODEBLOCK" + str(len(replacements)) + "\x00"
        replacements.append((placeholder, match.group(0)))
        masked = masked.replace(match.group(0), placeholder, 1)

    md_parts = re.split(r'(?=^#{1,4} )', masked, flags=re.MULTILINE)

    # Restore code blocks
    result = []
    for p in md_parts:
        for placeholder, original in replacements:
            p = p.replace(placeholder, original)
        if p.strip():
            result.append(p)
    return result


def _section_title(section: str) -> str:
    """Extract the heading text from a section's first line."""
    first_line = section.split("\n", 1)[0].strip()
    return first_line.lstrip("#").strip()


def _build_toc(sections: list[str]) -> list[dict[str, Any]]:
    """Build a table of contents from section list."""
    toc: list[dict[str, Any]] = []
    offset = 0
    for section in sections:
        first_line = section.split("\n", 1)[0].strip()
        level = 0
        for ch in first_line:
            if ch == "#":
                level += 1
            else:
                break
        toc.append({
            "title": _section_title(section),
            "level": max(level, 1),
            "char_offset": offset,
            "char_length": len(section),
        })
        offset += len(section)
    return toc


def _extract_section(content: str, section_name: str) -> str | None:
    """Find and return a specific section by heading text (case-insensitive)."""
    sections = _split_sections(content)
    target = section_name.lower().strip()
    for section in sections:
        title = _section_title(section).lower()
        if title == target or target in title:
            return section
    return None


def _extract_markdown_links(content: str) -> list[dict[str, str]]:
    """Extract markdown links from content, including absolute doc URLs."""
    import re
    from urllib.parse import urlparse
    pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(content):
        text, path = match.group(1), match.group(2)
        # Skip anchors-only and mailto
        if path.startswith(("mailto:", "#")):
            continue
        # For absolute URLs, extract the path portion as a potential doc reference
        if path.startswith(("http://", "https://")):
            parsed = urlparse(path)
            url_path = parsed.path.strip("/")
            if not url_path:
                continue
            # Add common doc extensions if missing
            if not any(url_path.endswith(ext) for ext in (".md", ".mdx", ".txt", ".rst")):
                url_path += ".md"
            clean_path = url_path
        else:
            clean_path = path.split("#")[0]
        if not clean_path:
            continue
        if clean_path in seen:
            continue
        seen.add(clean_path)
        links.append({"text": text, "path": clean_path})
    return links


def _clean_content(content: str) -> str:
    """Strip HTML tags, YAML frontmatter, and MDX syntax to produce clean markdown."""
    import re
    # Remove YAML frontmatter at start of file (---\n...\n---)
    content = re.sub(r'\A---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL)
    # Remove YAML frontmatter blocks anywhere in document (leakage from llms-full concatenation)
    # These appear as stray ---\nkey: value\n--- blocks not preceded by markdown content
    content = re.sub(r'\n---\n(?:[a-zA-Z_]+:[ \t]*[^\n]*\n)+---\s*\Z', '', content, flags=re.DOTALL)
    # Remove MDX import/export statements (import { X } from "..." / export ...)
    # Handle multi-line imports too (import {\n  X\n} from "...")
    content = re.sub(
        r'^(?:import|export)\s+(?:\{[^}]*\}|[\w*]+)\s+from\s+["\'][^"\']+["\'];?\s*$',
        '',
        content,
        flags=re.MULTILINE,
    )
    # Also strip bare export const/function/default lines
    content = re.sub(r'^export\s+(?:const|function|default|type|interface)\s+.*$', '', content, flags=re.MULTILINE)
    # Remove JSX/MDX component tags (lines that are just <Component /> or <Component>)
    content = re.sub(r'^\s*<[A-Z][A-Za-z]*[^>]*/>\s*$', '', content, flags=re.MULTILINE)
    # Remove HTML tags but keep content (e.g., <div class="...">text</div> → text)
    content = re.sub(r'<[^>]+>', '', content)
    # Collapse multiple blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


def _detect_locale(content: str) -> str:
    """Heuristic locale detection from document content."""
    import re

    sample = content[:5000].lower()

    # --- word-boundary helper ------------------------------------------
    # Short words (<=4 chars) use word-boundary regex to avoid matching
    # inside English words (e.g. "und" inside "fund").  Longer words are
    # distinctive enough for plain substring search.
    def _score(markers: list[str], text: str) -> int:
        hits = 0
        for m in markers:
            if len(m) <= 4:
                if re.search(rf"\b{re.escape(m)}\b", text):
                    hits += 1
            else:
                if m in text:
                    hits += 1
        return hits

    # German – common Stripe-doc words + everyday connectives
    # Avoid words shared with English (endpoint, integration, documentation).
    german_markers = [
        # longer / distinctive words (substring match)
        "verwendung", "beispiel", "funktion", "erstellen", "anleitung",
        "überblick", "zahlung", "dokumentation", "schritt",
        "hinzufügen", "konfigurieren", "können", "müssen", "registrieren",
        "überwachen", "ereignisse", "verarbeitet", "auslösen",
        # shorter words (word-boundary match)
        "und", "oder", "nicht", "eine", "wird",
    ]
    # French
    french_markers = [
        "utilisation", "exemple", "fonction", "créer", "aperçu",
        "paiement", "configurer", "étape",
        "ajouter", "événement", "automatique", "intégration",
        "également", "requête", "paramètre",
        "vous", "dans", "avec", "pour", "cette",
    ]
    # Spanish
    spanish_markers = [
        "utilización", "ejemplo", "función", "crear", "descripción",
        "documentación", "configuración", "paso", "agregar",
        "evento", "automático", "integración", "también",
        "solicitud", "parámetro", "añadir",
        "usted", "para", "esta", "como",
    ]

    de_score = _score(german_markers, sample)
    fr_score = _score(french_markers, sample)
    es_score = _score(spanish_markers, sample)

    if de_score >= 2:
        return "de"
    if fr_score >= 2:
        return "fr"
    if es_score >= 2:
        return "es"
    return "en"


def _truncate_to_token_budget(
    content: str, max_tokens: int, query: str | None = None,
) -> tuple[str, bool, list[dict[str, Any]] | None, int]:
    """Return content trimmed to fit within *max_tokens*.

    Strategy:
    1. If the whole document fits, return it unchanged.
    2. Split into markdown sections.  If a *query* is provided, score each
       section by keyword overlap and pick the highest-scoring ones first.
    3. Always include the first section (title / intro).
    4. Append a note showing how much was omitted.
    5. Return a full TOC so callers can request specific sections.

    Returns (text, was_truncated, table_of_contents_or_none, sections_omitted).
    """
    if _estimate_tokens(content) <= max_tokens:
        return content, False, None, 0

    char_budget = max_tokens * _CHARS_PER_TOKEN
    sections = _split_sections(content)

    if not sections:
        return content[:char_budget] + "\n\n---\n[Truncated — document too large]", True, None, 0

    # Build full TOC before selecting sections
    toc = _build_toc(sections)

    # Always keep the first section (title / intro), but truncate if it
    # alone exceeds the budget (common with monolith files).
    first = sections[0]
    if len(first) > char_budget:
        first = first[:char_budget - 200] + "\n[...section truncated]"
    selected: list[tuple[int, str]] = [(0, first)]
    used = len(first)

    # Score remaining sections by query relevance
    remaining = list(enumerate(sections[1:], start=1))
    if query:
        terms = [t.lower() for t in query.split() if len(t) > 1]

        def _score(section_text: str) -> int:
            lower = section_text.lower()
            return sum(lower.count(t) for t in terms)

        remaining.sort(key=lambda pair: _score(pair[1]), reverse=True)

    for idx, section in remaining:
        if used + len(section) > char_budget:
            # Try to fit a partial section
            room = char_budget - used
            if room > 200:
                selected.append((idx, section[:room] + "\n[...section truncated]"))
                used += room
            break
        selected.append((idx, section))
        used += len(section)

    # Reassemble in original document order
    selected.sort(key=lambda pair: pair[0])
    assembled = "".join(s for _, s in selected)

    omitted = len(sections) - len(selected)
    # Keep the inline note short — full TOC is in the structured `table_of_contents` field.
    # Embedding all section titles inline would blow past the token budget on large docs.
    note = f"\n\n---\n[Showing {len(selected)}/{len(sections)} sections"
    if omitted > 0:
        note += f" — {omitted} omitted"
    if query:
        note += f" — prioritized for: {query}"
    note += ". Use section= param with a title from table_of_contents to read a specific section.]\n"

    return assembled + note, True, toc, omitted


@dataclass(frozen=True)
class DocumentRecord:
    technology: str
    rel_path: str
    title: str
    version: str | None
    checksum: str
    source_path: str


@dataclass(frozen=True)
class UpdateEvent:
    id: int
    project_id: str
    technology: str
    rel_path: str
    title: str
    version: str | None
    event_type: str
    detected_at: str
    source_path: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


class DocsHubService:
    def __init__(
        self,
        base_dir: Path | str,
        retriever: HybridRetriever | None = None,
        indexer: Any | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.docs_root = self.base_dir / "docs_center"
        self.projects_root = self.docs_root / "projects"
        self.technologies_root = self.docs_root / "technologies"
        self.state_dir = self.base_dir / "state"
        self.db_path = self.state_dir / "buonaiuto_doc4llm.db"
        self.retriever = retriever or HybridRetriever()
        self.indexer = indexer  # DocIndexer | None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_file TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_subscriptions (
                    project_id TEXT NOT NULL,
                    technology TEXT NOT NULL,
                    PRIMARY KEY (project_id, technology),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS project_cursors (
                    project_id TEXT PRIMARY KEY,
                    last_seen_event_id INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    technology TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT,
                    checksum TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    last_scanned_at TEXT NOT NULL,
                    PRIMARY KEY (technology, rel_path)
                );

                CREATE TABLE IF NOT EXISTS update_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    technology TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT,
                    checksum TEXT,
                    event_type TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    source_path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fetch_state (
                    technology       TEXT    NOT NULL PRIMARY KEY,
                    source_url       TEXT    NOT NULL,
                    etag             TEXT,
                    last_modified    TEXT,
                    last_fetched_at  TEXT    NOT NULL,
                    last_status_code INTEGER NOT NULL DEFAULT 200,
                    bytes_received   INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    technology    TEXT    NOT NULL,
                    rel_path      TEXT    NOT NULL,
                    query         TEXT    NOT NULL,
                    satisfied     INTEGER NOT NULL,
                    reason        TEXT    NOT NULL,
                    requester_id  TEXT    NOT NULL,
                    created_at    TEXT    NOT NULL
                );
                """
            )

    def scan(self) -> dict[str, Any]:
        self.sync_projects()
        summaries: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        scanned_at = utc_now()
        if not self.technologies_root.exists():
            return {
                "scanned_at": scanned_at,
                "technologies": summaries,
                "total_documents": 0,
                "total_events": 0,
            }

        for tech_dir in sorted(self.technologies_root.iterdir()):
            if not tech_dir.is_dir():
                continue
            technology = tech_dir.name
            manifest = self._read_manifest(tech_dir)
            version = manifest.get("version")
            current_docs = self._collect_documents(tech_dir, technology, version)
            with self._connect() as conn:
                existing = {
                    (row["technology"], row["rel_path"]): row
                    for row in conn.execute(
                        "SELECT * FROM documents WHERE technology = ?",
                        (technology,),
                    ).fetchall()
                }

                tech_events = 0
                for record in current_docs:
                    key = (record.technology, record.rel_path)
                    seen_keys.add(key)
                    previous = existing.get(key)
                    event_type = None

                    if previous is None:
                        event_type = "added"
                    elif previous["checksum"] != record.checksum or previous["version"] != record.version:
                        event_type = "updated"

                    conn.execute(
                        """
                        INSERT INTO documents (
                            technology, rel_path, title, version, checksum, source_path, last_scanned_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(technology, rel_path) DO UPDATE SET
                            title = excluded.title,
                            version = excluded.version,
                            checksum = excluded.checksum,
                            source_path = excluded.source_path,
                            last_scanned_at = excluded.last_scanned_at
                        """,
                        (
                            record.technology,
                            record.rel_path,
                            record.title,
                            record.version,
                            record.checksum,
                            record.source_path,
                            utc_now(),
                        ),
                    )

                    if event_type:
                        tech_events += 1
                        conn.execute(
                            """
                            INSERT INTO update_events (
                                technology, rel_path, title, version, checksum, event_type, detected_at, source_path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                record.technology,
                                record.rel_path,
                                record.title,
                                record.version,
                                record.checksum,
                                event_type,
                                utc_now(),
                                record.source_path,
                            ),
                        )

                current_keys = {(doc.technology, doc.rel_path) for doc in current_docs}
                missing = [
                    row
                    for key, row in existing.items()
                    if key not in current_keys
                ]
                for row in missing:
                    tech_events += 1
                    conn.execute(
                        """
                        INSERT INTO update_events (
                            technology, rel_path, title, version, checksum, event_type, detected_at, source_path
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["technology"],
                            row["rel_path"],
                            row["title"],
                            row["version"],
                            row["checksum"],
                            "deleted",
                            utc_now(),
                            row["source_path"],
                        ),
                    )
                    conn.execute(
                        "DELETE FROM documents WHERE technology = ? AND rel_path = ?",
                        (row["technology"], row["rel_path"]),
                    )

                summaries.append(
                    {
                        "technology": technology,
                        "documents_indexed": len(current_docs),
                        "events_created": tech_events,
                        "version": version,
                    }
                )

            # Trigger Qdrant indexing when new/updated docs are detected
            if self.indexer is not None and tech_events > 0:
                self.indexer.index_technology(technology)

        # On first scan with an indexer, index all technologies if Qdrant is empty
        if self.indexer is not None and summaries:
            try:
                info = self.indexer.qdrant_client.client.get_collection(
                    self.indexer.qdrant_client.collection_name,
                )
                if (info.points_count or 0) == 0:
                    for s in summaries:
                        self.indexer.index_technology(s["technology"])
            except Exception:
                pass

        return {
            "scanned_at": scanned_at,
            "technologies": summaries,
            "total_documents": sum(s["documents_indexed"] for s in summaries),
            "total_events": sum(s["events_created"] for s in summaries),
        }

    def sync_projects(self) -> list[dict[str, Any]]:
        if not self.projects_root.exists():
            return []

        synced: list[dict[str, Any]] = []
        for project_file in sorted(self.projects_root.glob("*.json")):
            payload = read_json(project_file)
            project_id = payload["project_id"]
            name = payload.get("name", project_id)
            technologies = sorted(set(payload.get("technologies", [])))

            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO projects(project_id, name, source_file)
                    VALUES (?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        name = excluded.name,
                        source_file = excluded.source_file
                    """,
                    (project_id, name, str(project_file)),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO project_cursors(project_id, last_seen_event_id) VALUES (?, 0)",
                    (project_id,),
                )
                conn.execute(
                    "DELETE FROM project_subscriptions WHERE project_id = ?",
                    (project_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO project_subscriptions(project_id, technology)
                    VALUES (?, ?)
                    """,
                    [(project_id, tech) for tech in technologies],
                )

            synced.append(
                {
                    "project_id": project_id,
                    "name": name,
                    "technologies": technologies,
                }
            )
        return synced

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.project_id, p.name,
                       COALESCE(pc.last_seen_event_id, 0) AS last_seen_event_id
                FROM projects p
                LEFT JOIN project_cursors pc ON pc.project_id = p.project_id
                ORDER BY p.project_id
                """
            ).fetchall()
            result = []
            for row in rows:
                techs = [
                    tech_row["technology"]
                    for tech_row in conn.execute(
                        "SELECT technology FROM project_subscriptions WHERE project_id = ? ORDER BY technology",
                        (row["project_id"],),
                    ).fetchall()
                ]
                result.append(
                    {
                        "project_id": row["project_id"],
                        "name": row["name"],
                        "technologies": techs,
                        "last_seen_event_id": row["last_seen_event_id"],
                    }
                )
            return result

    def list_project_updates(
        self, project_id: str, unread_only: bool = True, limit: int = 20
    ) -> dict[str, Any]:
        with self._connect() as conn:
            cursor_row = conn.execute(
                "SELECT last_seen_event_id FROM project_cursors WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if cursor_row is None:
                raise ValueError(f"Unknown project_id: {project_id}")
            last_seen = int(cursor_row["last_seen_event_id"])

            # Get total unseen count
            unseen_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt, MAX(ue.id) AS max_id
                FROM update_events ue
                INNER JOIN project_subscriptions ps ON ps.technology = ue.technology
                WHERE ps.project_id = ? AND ue.id > ?
                """,
                (project_id, last_seen),
            ).fetchone()
            unseen_count = int(unseen_row["cnt"] or 0)
            latest_event_id = int(unseen_row["max_id"] or 0) if unseen_row["max_id"] else None

            clause = "AND ue.id > ?" if unread_only else ""
            params: list[Any] = [project_id]
            if unread_only:
                params.append(last_seen)
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT ue.*
                FROM update_events ue
                INNER JOIN project_subscriptions ps
                    ON ps.technology = ue.technology
                WHERE ps.project_id = ?
                {clause}
                ORDER BY ue.id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()

        events = [
            UpdateEvent(
                id=row["id"],
                project_id=project_id,
                technology=row["technology"],
                rel_path=row["rel_path"],
                title=row["title"],
                version=row["version"],
                event_type=row["event_type"],
                detected_at=row["detected_at"],
                source_path=row["source_path"],
            ).__dict__
            for row in rows
        ]
        return {
            "project_id": project_id,
            "unseen_count": unseen_count,
            "latest_event_id": latest_event_id,
            "last_seen_event_id": last_seen,
            "events": events,
        }

    def ack_project_updates(self, project_id: str, through_event_id: int | None = None) -> int:
        with self._connect() as conn:
            if through_event_id is None:
                row = conn.execute(
                    """
                    SELECT MAX(ue.id) AS max_id
                    FROM update_events ue
                    INNER JOIN project_subscriptions ps
                        ON ps.technology = ue.technology
                    WHERE ps.project_id = ?
                    """,
                    (project_id,),
                ).fetchone()
                through_event_id = int(row["max_id"] or 0)

            conn.execute(
                """
                INSERT INTO project_cursors(project_id, last_seen_event_id)
                VALUES (?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    last_seen_event_id = excluded.last_seen_event_id
                """,
                (project_id, through_event_id),
            )
        return through_event_id

    def _resolve_related_docs(
        self, technology: str, rel_path: str, content: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Extract markdown links from content and resolve against the documents table."""
        raw_links = _extract_markdown_links(content)
        if not raw_links:
            return []

        from posixpath import normpath, dirname, join as pjoin
        doc_dir = dirname(rel_path)

        related: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        with self._connect() as conn:
            for link in raw_links:
                if len(related) >= limit:
                    break
                # Try multiple resolution strategies
                candidates = [
                    normpath(pjoin(doc_dir, link["path"])),  # relative
                    link["path"],  # as-is (for URL-derived paths)
                    f"docs/{link['path']}",  # under docs/ prefix
                ]
                for candidate in candidates:
                    if candidate in seen_paths:
                        continue
                    row = conn.execute(
                        "SELECT title, rel_path FROM documents WHERE technology = ? AND rel_path = ?",
                        (technology, candidate),
                    ).fetchone()
                    if row is not None:
                        seen_paths.add(candidate)
                        related.append({
                            "technology": technology,
                            "rel_path": row["rel_path"],
                            "title": row["title"],
                            "link_text": link["text"],
                        })
                        break
        return related

    def read_doc(
        self,
        technology: str,
        rel_path: str,
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
        query: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT title, version, source_path, last_scanned_at
                FROM documents
                WHERE technology = ? AND rel_path = ?
                """,
                (technology, rel_path),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown document: {technology}/{rel_path}")

            # Fetch freshness from fetch_state if available
            fetch_row = conn.execute(
                "SELECT last_fetched_at FROM fetch_state WHERE technology = ?",
                (technology,),
            ).fetchone()

        source_path = Path(row["source_path"]).resolve()
        if not source_path.is_relative_to(self.base_dir.resolve()):
            raise ValueError(f"Document path outside base directory: {source_path}")

        full_content = source_path.read_text(encoding="utf-8")

        # Resolve template references (e.g. FastAPI {* path *} syntax)
        if "{*" in full_content:
            try:
                from ingestion.template_resolver import resolve_templates
                full_content = resolve_templates(full_content, source_path)
            except ImportError:
                pass  # template_resolver not available

        # Clean HTML tags and frontmatter for LLM-friendly output
        full_content = _clean_content(full_content)

        full_tokens = _estimate_tokens(full_content)
        char_count = len(full_content)
        locale = _detect_locale(full_content)

        # Resolve related docs from markdown links
        related_docs = self._resolve_related_docs(technology, rel_path, full_content)

        # Section-level reading: return just the requested section
        if section is not None:
            extracted = _extract_section(full_content, section)
            if extracted is None:
                sections = _split_sections(full_content)
                available = [_section_title(s) for s in sections]
                raise ValueError(
                    f"Section not found: '{section}' (document locale: {locale}). "
                    f"Available sections: {available}"
                )
            returned_tokens = _estimate_tokens(extracted)
            return {
                "technology": technology,
                "rel_path": rel_path,
                "title": row["title"],
                "version": row["version"],
                "source_path": str(source_path),
                "content": extracted,
                "section_match": _section_title(extracted),
                "total_tokens": full_tokens,
                "returned_tokens": returned_tokens,
                "char_count": char_count,
                "truncated": False,
                "sections_omitted": 0,
                "table_of_contents": None,
                "related_docs": related_docs,
                "last_scanned_at": row["last_scanned_at"],
                "last_fetched_at": fetch_row["last_fetched_at"] if fetch_row else None,
                "locale": locale,
            }

        # Full-doc reading with token budget
        sections_omitted = 0
        if max_tokens is not None and max_tokens > 0:
            content, truncated, toc, sections_omitted = _truncate_to_token_budget(full_content, max_tokens, query=query)
        else:
            content, truncated, toc = full_content, False, None

        # Always provide TOC for docs with multiple sections (even if not truncated)
        if toc is None:
            sections = _split_sections(full_content)
            if len(sections) > 1:
                toc = _build_toc(sections)

        returned_tokens = _estimate_tokens(content)
        return {
            "technology": technology,
            "rel_path": rel_path,
            "title": row["title"],
            "version": row["version"],
            "source_path": str(source_path),
            "content": content,
            "total_tokens": full_tokens,
            "returned_tokens": returned_tokens,
            "char_count": char_count,
            "truncated": truncated,
            "sections_omitted": sections_omitted,
            "table_of_contents": toc,
            "related_docs": related_docs,
            "last_scanned_at": row["last_scanned_at"],
            "last_fetched_at": fetch_row["last_fetched_at"] if fetch_row else None,
            "locale": locale,
        }

    def read_full_page(
        self,
        library_id: str,
        version: str | None,
        rel_path: str,
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
        query: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        payload = self.read_doc(
            technology=library_id, rel_path=rel_path,
            max_tokens=max_tokens, query=query, section=section,
        )
        if version is not None and payload["version"] != version:
            raise ValueError(
                f"Requested version '{version}' does not match stored version '{payload['version']}'."
            )
        return payload

    def list_supported_libraries(self) -> list[dict[str, Any]]:
        _MONOLITH_CHAR_THRESHOLD = 100_000
        _HTML_ERROR_SIGNATURES = (
            "<!DOCTYPE html", "<html", "cloudflare", "error 404", "not found",
            "access denied", "403 forbidden",
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT technology AS library_id, version, COUNT(*) AS documents_count,
                       MAX(last_scanned_at) AS last_scanned_at
                FROM documents
                GROUP BY technology, version
                ORDER BY technology, version
                """
            ).fetchall()

            # Collect fetch state per technology
            fetch_rows: dict[str, str | None] = {
                row["technology"]: row["last_fetched_at"]
                for row in conn.execute(
                    "SELECT technology, last_fetched_at FROM fetch_state"
                ).fetchall()
            }

            # Detect monolith libraries: few docs AND file > threshold
            monolith_techs: set[str] = set()
            broken_techs: set[str] = set()
            for row in rows:
                if row["documents_count"] <= 2:
                    mono_row = conn.execute(
                        "SELECT source_path FROM documents WHERE technology = ? LIMIT 1",
                        (row["library_id"],),
                    ).fetchone()
                    if mono_row:
                        try:
                            size = Path(mono_row["source_path"]).stat().st_size
                            if size > _MONOLITH_CHAR_THRESHOLD:
                                monolith_techs.add(row["library_id"])
                            # Detect broken/404 content
                            sample = Path(mono_row["source_path"]).read_text(
                                encoding="utf-8", errors="replace"
                            )[:2000].lower()
                            if any(sig in sample for sig in _HTML_ERROR_SIGNATURES):
                                broken_techs.add(row["library_id"])
                        except OSError:
                            pass

        return [
            {
                "library_id": row["library_id"],
                "version": row["version"],
                "documents_count": row["documents_count"],
                "monolith": row["library_id"] in monolith_techs,
                "status": "broken" if row["library_id"] in broken_techs else "ok",
                "last_scanned_at": row["last_scanned_at"],
                "last_fetched_at": fetch_rows.get(row["library_id"]),
            }
            for row in rows
        ]

    def list_docs(
        self,
        technology: str,
        path_prefix: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List documents for a technology, optionally filtered by path prefix."""
        with self._connect() as conn:
            if path_prefix:
                rows = conn.execute(
                    """
                    SELECT technology, rel_path, title, version,
                           last_scanned_at, source_path
                    FROM documents
                    WHERE technology = ? AND rel_path LIKE ?
                    ORDER BY rel_path
                    LIMIT ?
                    """,
                    (technology, f"{path_prefix}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT technology, rel_path, title, version,
                           last_scanned_at, source_path
                    FROM documents
                    WHERE technology = ?
                    ORDER BY rel_path
                    LIMIT ?
                    """,
                    (technology, limit),
                ).fetchall()

            fetch_row = conn.execute(
                "SELECT last_fetched_at FROM fetch_state WHERE technology = ?",
                (technology,),
            ).fetchone()
            last_fetched_at = fetch_row["last_fetched_at"] if fetch_row else None

        result = []
        for row in rows:
            # Use cleaned content length for consistency with read_doc
            try:
                raw = Path(row["source_path"]).read_text(encoding="utf-8")
                char_count = len(_clean_content(raw))
            except OSError:
                char_count = 0
            result.append({
                "technology": row["technology"],
                "rel_path": row["rel_path"],
                "title": row["title"],
                "version": row["version"],
                "char_count": char_count,
                "last_scanned_at": row["last_scanned_at"],
                "last_fetched_at": last_fetched_at,
            })
        return result

    def diff_since(
        self,
        since: str,
        technology: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return documentation changes since a given ISO timestamp.

        Supports cursor/offset pagination via offset parameter.
        Supports filtering by event_type ('added', 'updated', 'deleted').
        """
        with self._connect() as conn:
            clauses = ["detected_at > ?"]
            params: list[Any] = [since]
            if technology:
                clauses.append("technology = ?")
                params.append(technology)
            if event_type:
                clauses.append("event_type = ?")
                params.append(event_type)
            where = " AND ".join(clauses)

            count_row = conn.execute(
                f"SELECT COUNT(*) FROM update_events WHERE {where}",
                tuple(params),
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT id, technology, rel_path, title, event_type, detected_at
                FROM update_events
                WHERE {where}
                ORDER BY id DESC LIMIT ? OFFSET ?
                """,
                tuple(params) + (limit, offset),
            ).fetchall()

        total_count = count_row[0] if count_row else 0
        events = [
            {
                "id": row["id"],
                "technology": row["technology"],
                "rel_path": row["rel_path"],
                "title": row["title"],
                "event_type": row["event_type"],
                "detected_at": row["detected_at"],
            }
            for row in rows
        ]
        return {
            "events": events,
            "total_count": total_count,
            "has_more": (offset + len(events)) < total_count,
            "limit": limit,
            "offset": offset,
        }

    def search_documentation(
        self,
        query: str,
        libraries: list[dict[str, Any]] | None = None,
        limit: int = 5,
        workspace_id: str = "local",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_trace_id = ensure_trace_id(trace_id)
        selected_library: str | None = None
        selected_version: str | None = None
        requested_libraries: list[str] = []
        version_filters: dict[str, set[str] | None] = {}
        if libraries:
            for item in libraries:
                if not isinstance(item, dict):
                    continue
                raw_library_id = item.get("id")
                if not isinstance(raw_library_id, str):
                    continue
                library_id = raw_library_id.strip()
                if not library_id:
                    continue
                requested_libraries.append(library_id)
                raw_version = item.get("version")
                if raw_version is None:
                    # No version = match all versions for this library
                    version_filters[library_id] = None
                    continue
                if not isinstance(raw_version, str):
                    continue
                version = raw_version.strip()
                if not version:
                    version_filters[library_id] = None
                    continue
                # Once set to None (match all), don't narrow back to a specific version
                if version_filters.get(library_id) is None and library_id in version_filters:
                    continue
                current = version_filters.get(library_id)
                if current is None:
                    version_filters[library_id] = {version}
                else:
                    current.add(version)

            requested_libraries = list(dict.fromkeys(requested_libraries))
            if len(requested_libraries) == 1:
                selected_library = requested_libraries[0]
                allowed_versions = version_filters.get(selected_library)
                if isinstance(allowed_versions, set) and len(allowed_versions) == 1:
                    selected_version = next(iter(allowed_versions))

        # Try vector search first (avoids loading all file content into memory)
        rq = RetrievalQuery(
            text=query,
            workspace_id=workspace_id,
            library_id=selected_library,
            version=selected_version,
            limit=limit,
        )
        hybrid_result = self.retriever._search_hybrid(rq)
        if hybrid_result:
            terms = [t for t in query.lower().split()
                     if t not in self.retriever._STOP_WORDS and len(t) > 1]
            if not terms:
                terms = query.lower().split()
            reranked = self.retriever._rerank_hybrid(hybrid_result, terms, query.lower())
            retrieval = RetrievalResponse(
                retrieval_mode="hybrid", matches=reranked[:limit],
            )
        else:
            # Fallback: lexical search (loads content from disk)
            with self._connect() as conn:
                if not requested_libraries:
                    rows = conn.execute(
                        """
                        SELECT technology, title, rel_path, version, source_path
                        FROM documents
                        ORDER BY technology, rel_path
                        LIMIT 2000
                        """
                    ).fetchall()
                else:
                    placeholders = ", ".join(["?"] * len(requested_libraries))
                    rows = conn.execute(
                        f"""
                        SELECT technology, title, rel_path, version, source_path
                        FROM documents
                        WHERE technology IN ({placeholders})
                        ORDER BY rel_path
                        """,
                        tuple(requested_libraries),
                    ).fetchall()

            if requested_libraries:
                filtered_rows: list[Any] = []
                for row in rows:
                    allowed_versions = version_filters.get(row["technology"])
                    if allowed_versions is None:
                        filtered_rows.append(row)
                        continue
                    if row["version"] in allowed_versions:
                        filtered_rows.append(row)
                rows = filtered_rows

            documents = []
            for row in rows:
                source = Path(row["source_path"])
                # Skip monolith files that have been split into individual
                # topic pages — the split directory shares the stem name.
                if source.suffix in (".txt", ".md", ".mdx"):
                    split_dir = source.parent / source.stem
                    if split_dir.is_dir() and any(split_dir.iterdir()):
                        continue
                try:
                    content = source.read_text(encoding="utf-8")
                except OSError:
                    continue
                documents.append(
                    RetrievalDocument(
                        workspace_id=workspace_id,
                        library_id=row["technology"],
                        version=row["version"],
                        rel_path=row["rel_path"],
                        title=row["title"],
                        content=content,
                        source_uri=self.doc_uri(row["technology"], row["rel_path"]),
                    )
                )
            retrieval = self.retriever.search(documents, rq)

        # Enrich results with file sizes and freshness from the DB
        doc_meta: dict[tuple[str, str], dict[str, Any]] = {}
        match_keys = [(m.library_id, m.rel_path) for m in retrieval.matches]
        if match_keys:
            with self._connect() as conn:
                for tech, rp in match_keys:
                    if (tech, rp) in doc_meta:
                        continue
                    meta_row = conn.execute(
                        "SELECT source_path, last_scanned_at FROM documents WHERE technology = ? AND rel_path = ?",
                        (tech, rp),
                    ).fetchone()
                    if meta_row:
                        try:
                            char_count = Path(meta_row["source_path"]).stat().st_size
                        except OSError:
                            char_count = 0
                        doc_meta[(tech, rp)] = {
                            "char_count": char_count,
                            "last_scanned_at": meta_row["last_scanned_at"],
                        }

        # Deduplicate search results by (technology, rel_path) AND by content
        # checksum (catches monolith files indexed at multiple paths).
        seen_docs: set[tuple[str, str]] = set()
        seen_checksums: set[str] = set()
        results: list[dict[str, Any]] = []

        # Build checksum lookup for dedup
        checksum_map: dict[tuple[str, str], str] = {}
        if retrieval.matches:
            with self._connect() as conn:
                for match in retrieval.matches:
                    row = conn.execute(
                        "SELECT checksum FROM documents WHERE technology = ? AND rel_path = ?",
                        (match.library_id, match.rel_path),
                    ).fetchone()
                    if row:
                        checksum_map[(match.library_id, match.rel_path)] = row["checksum"]

        for match in retrieval.matches:
            key = (match.library_id, match.rel_path)
            if key in seen_docs:
                continue
            # Skip duplicate content (same checksum at different paths)
            checksum = checksum_map.get(key)
            if checksum and checksum in seen_checksums:
                continue
            seen_docs.add(key)
            if checksum:
                seen_checksums.add(checksum)
            results.append({
                "technology": match.library_id,
                "title": match.title,
                "rel_path": match.rel_path,
                "version": match.version,
                "snippet": match.snippet,
                "uri": match.source_uri,
                "score": match.score,
                "retrieval_mode": retrieval.retrieval_mode,
                "workspace_id": match.workspace_id,
                "trace_id": resolved_trace_id,
                "char_count": doc_meta.get(key, {}).get("char_count", 0),
                "last_scanned_at": doc_meta.get(key, {}).get("last_scanned_at"),
            })

        # Per-library minimum — ensure at least 1 result per requested library.
        # Runs for all requested libraries (including single-library searches with 0 results).
        if requested_libraries:
            represented = {r["technology"] for r in results}
            for lib_id in requested_libraries:
                if lib_id not in represented:
                    sub_query = RetrievalQuery(
                        text=query, workspace_id=workspace_id,
                        library_id=lib_id, limit=3,
                    )
                    with self._connect() as conn:
                        sub_rows = conn.execute(
                            "SELECT technology, title, rel_path, version, source_path "
                            "FROM documents WHERE technology = ? LIMIT 200",
                            (lib_id,),
                        ).fetchall()
                    sub_docs = []
                    for row in sub_rows:
                        try:
                            content = Path(row["source_path"]).read_text(encoding="utf-8")
                        except OSError:
                            continue
                        sub_docs.append(RetrievalDocument(
                            workspace_id=workspace_id, library_id=row["technology"],
                            version=row["version"], rel_path=row["rel_path"],
                            title=row["title"], content=content,
                            source_uri=self.doc_uri(row["technology"], row["rel_path"]),
                        ))
                    if sub_docs:
                        sub_result = self.retriever.search(sub_docs, sub_query)
                        best = sub_result.matches[:1]
                        # If BM25 found nothing (no term overlap), surface top doc as best-effort
                        if not best:
                            first_doc = sub_docs[0]
                            from retrieval.retriever import RetrievalMatch
                            best = [RetrievalMatch(
                                workspace_id=first_doc.workspace_id,
                                library_id=first_doc.library_id,
                                version=first_doc.version,
                                rel_path=first_doc.rel_path,
                                title=first_doc.title,
                                source_uri=first_doc.source_uri,
                                score=0.0,
                                snippet=first_doc.content[:300].strip(),
                            )]
                        for m in best:
                            mk = (m.library_id, m.rel_path)
                            meta = doc_meta.get(mk, {})
                            results.append({
                                "technology": m.library_id,
                                "title": m.title,
                                "rel_path": m.rel_path,
                                "version": m.version,
                                "snippet": m.snippet,
                                "uri": m.source_uri,
                                "score": m.score,
                                "retrieval_mode": "fallback",
                                "workspace_id": m.workspace_id,
                                "trace_id": resolved_trace_id,
                                "char_count": meta.get("char_count", 0),
                                "last_scanned_at": meta.get("last_scanned_at"),
                            })

        # Build per-library result counts for caller transparency.
        # Always include all requested libraries so 0-result libs are visible.
        result_count_by_library: dict[str, int] = {lib: 0 for lib in requested_libraries}
        for r in results:
            tech = r["technology"]
            result_count_by_library[tech] = result_count_by_library.get(tech, 0) + 1

        return {
            "retrieval_mode": retrieval.retrieval_mode,
            "library_id": selected_library,
            "version": selected_version,
            "requested_libraries": requested_libraries,
            "result_count_by_library": result_count_by_library,
            "workspace_id": workspace_id,
            "trace_id": resolved_trace_id,
            "results": results,
        }

    def search_docs(self, technology: str, query: str, limit: int = 5) -> dict[str, Any]:
        """Search docs for a single technology. Returns the same envelope as search_documentation."""
        return self.search_documentation(
            query=query,
            libraries=[{"id": technology}],
            limit=limit,
            workspace_id="local",
        )

    def install_project(
        self,
        project_root: Path | str,
        project_id: str | None = None,
        seed_technologies_root: Path | str | None = None,
    ) -> dict[str, Any]:
        from .auto_setup import ingest_local_llms_files

        install_summary = bootstrap_project(
            base_dir=self.base_dir,
            project_root=project_root,
            project_id=project_id,
            seed_technologies_root=seed_technologies_root,
        )

        # Step 1 — ingest any llms.txt / llms-full.txt files found locally in
        # the project tree.  These are available immediately without any HTTP
        # requests and do not count against the web-fetch quota.
        local_result = ingest_local_llms_files(project_root, self.base_dir)
        locally_satisfied: set[str] = set(local_result["ingested"])

        # Step 2 — fetch from the web for all remaining detected technologies
        # that were NOT satisfied by a local file.
        detected = install_summary.get("technologies_detected", [])
        needs_fetch = [t for t in detected if t not in locally_satisfied]
        fetched: list[dict[str, Any]] = []
        fetch_errors: list[dict[str, str]] = []
        if needs_fetch:
            from ingestion.http_fetcher import HttpDocFetcher
            from ingestion.registry_loader import default_registry_path, load_registry

            mappings = load_registry(default_registry_path())
            fetcher = HttpDocFetcher(
                base_dir=self.base_dir,
                db_path=self.db_path,
                registry=mappings,
            )
            for tech in needs_fetch:
                try:
                    fetched.append(fetcher.fetch(tech))
                except (ValueError, RuntimeError) as exc:
                    fetch_errors.append({"technology": tech, "error": str(exc)})

        install_summary["local_ingested"] = sorted(locally_satisfied)
        install_summary["local_ingest_errors"] = local_result["errors"]
        install_summary["fetch_results"] = fetched
        install_summary["fetch_errors"] = fetch_errors
        install_summary["scan_summary"] = self.scan()
        return install_summary

    def fetch_docs(
        self,
        technology: str | None = None,
        registry_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Fetch fresh documentation from the web and re-scan.

        Args:
            technology: if given, fetch only this technology; otherwise fetch all.
            registry_path: path to the library registry JSON file.
                           Defaults to the bundled ``src/ingestion/registry.json``.

        Returns:
            Dict with ``fetch_results`` and ``scan_summary``.

        Raises:
            RuntimeError: on HTTP errors or if requests is not installed.
            ValueError: if the technology is not in the registry.
        """
        from ingestion.http_fetcher import HttpDocFetcher
        from ingestion.registry_loader import default_registry_path, load_registry

        rp = Path(registry_path) if registry_path is not None else default_registry_path()
        mappings = load_registry(rp)
        fetcher = HttpDocFetcher(
            base_dir=self.base_dir,
            db_path=self.db_path,
            registry=mappings,
        )

        if technology is not None:
            fetch_results = [fetcher.fetch(technology)]
        else:
            fetch_results = fetcher.fetch_all()

        scan_summary = self.scan()
        return {"fetch_results": fetch_results, "scan_summary": scan_summary}

    def build_update_prompt(self, project_id: str, limit: int = 10) -> str:
        payload = self.list_project_updates(project_id, unread_only=True, limit=limit)
        updates = payload["events"]
        if not updates:
            return (
                f"No unread documentation updates are currently recorded for project '{project_id}'. "
                "Continue using the existing local documentation set."
            )

        lines = [
            f"Project '{project_id}' has unread local documentation updates.",
            "Review the updated documents before answering implementation questions.",
            "",
            "Updated documents:",
        ]
        for event in reversed(updates):
            version_part = f" version={event['version']}" if event.get("version") else ""
            lines.append(
                f"- [{event['event_type']}] {event['technology']}/{event['rel_path']}{version_part} uri={self.doc_uri(event['technology'], event['rel_path'])}"
            )

        lines.extend(
            [
                "",
                "Instructions:",
                "1. Read the listed local document URIs before answering.",
                "2. Prefer the updated local docs over stale prior knowledge.",
                "3. If multiple docs conflict, use the newest local update event.",
            ]
        )
        return "\n".join(lines)

    def list_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        for project in self.list_projects():
            resources.append(
                {
                    "uri": f"updates://{project['project_id']}",
                    "name": f"Unread updates for {project['project_id']}",
                    "mimeType": "application/json",
                }
            )

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT technology, rel_path, title FROM documents ORDER BY technology, rel_path"
            ).fetchall()
        for row in rows:
            resources.append(
                {
                    "uri": self.doc_uri(row["technology"], row["rel_path"]),
                    "name": f"{row['technology']} :: {row['title']}",
                    "mimeType": "text/markdown",
                }
            )
        return resources

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri.startswith("updates://"):
            project_id = uri.removeprefix("updates://")
            payload = self.list_project_updates(project_id, unread_only=True, limit=50)
            return {"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}

        if uri.startswith("doc://"):
            remainder = uri.removeprefix("doc://")
            if "/" not in remainder:
                raise ValueError("Malformed doc URI: expected doc://<technology>/<rel_path>")
            technology, rel_path = remainder.split("/", 1)
            if not technology or not rel_path:
                raise ValueError("Malformed doc URI: expected doc://<technology>/<rel_path>")
            doc = self.read_doc(technology, rel_path)
            return {"uri": uri, "mimeType": "text/markdown", "text": doc["content"]}

        raise ValueError(f"Unsupported resource URI: {uri}")

    def submit_feedback(
        self,
        technology: str,
        rel_path: str,
        query: str,
        satisfied: bool,
        reason: str,
        requester_id: str,
    ) -> dict[str, Any]:
        if satisfied is None or not isinstance(satisfied, bool):
            raise ValueError("satisfied must be a boolean (true/false)")
        if not reason or not reason.strip():
            raise ValueError("reason is required — explain why the documentation did or did not help")

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback (technology, rel_path, query, satisfied, reason, requester_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (technology, rel_path, query, int(satisfied), reason.strip(), requester_id, utc_now()),
            )
            feedback_id = cursor.lastrowid

        return {
            "id": feedback_id,
            "technology": technology,
            "rel_path": rel_path,
            "query": query,
            "satisfied": satisfied,
            "reason": reason.strip(),
            "requester_id": requester_id,
        }

    def list_feedback(
        self,
        technology: str | None = None,
        limit: int = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """List feedback entries, most recent first. Supports time-range filtering."""
        with self._connect() as conn:
            clauses = []
            params: list[Any] = []
            if technology is not None:
                clauses.append("technology = ?")
                params.append(technology)
            if since is not None:
                clauses.append("created_at >= ?")
                params.append(since)
            if until is not None:
                clauses.append("created_at <= ?")
                params.append(until)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM feedback{where} ORDER BY id DESC LIMIT ?",
                tuple(params),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "technology": row["technology"],
                "rel_path": row["rel_path"],
                "query": row["query"],
                "satisfied": bool(row["satisfied"]),
                "reason": row["reason"],
                "requester_id": row["requester_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def feedback_stats(
        self,
        technology: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate feedback statistics. Supports time-range filtering."""
        with self._connect() as conn:
            clauses = []
            params: list[Any] = []
            if technology is not None:
                clauses.append("technology = ?")
                params.append(technology)
            if since is not None:
                clauses.append("created_at >= ?")
                params.append(since)
            if until is not None:
                clauses.append("created_at <= ?")
                params.append(until)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

            agg = conn.execute(
                f"SELECT COUNT(*) AS total, SUM(satisfied) AS satisfied FROM feedback{where}",
                tuple(params),
            ).fetchone()
            doc_rows = conn.execute(
                f"""
                SELECT technology, rel_path,
                       COUNT(*) AS total,
                       SUM(satisfied) AS satisfied
                FROM feedback{where}
                GROUP BY technology, rel_path
                ORDER BY total DESC
                """,
                tuple(params),
            ).fetchall()

        total = int(agg["total"])
        satisfied = int(agg["satisfied"] or 0)
        unsatisfied = total - satisfied
        rate = round(satisfied / total, 4) if total > 0 else 0.0

        by_document = [
            {
                "technology": row["technology"],
                "rel_path": row["rel_path"],
                "total": int(row["total"]),
                "satisfied": int(row["satisfied"] or 0),
                "unsatisfied": int(row["total"]) - int(row["satisfied"] or 0),
                "satisfaction_rate": round(int(row["satisfied"] or 0) / int(row["total"]), 4) if int(row["total"]) > 0 else 0.0,
            }
            for row in doc_rows
        ]

        # Per-technology breakdown
        by_technology: dict[str, dict[str, int]] = {}
        for d in by_document:
            tech = d["technology"]
            if tech not in by_technology:
                by_technology[tech] = {"total": 0, "satisfied": 0}
            by_technology[tech]["total"] += d["total"]
            by_technology[tech]["satisfied"] += d["satisfied"]

        tech_stats = [
            {
                "technology": tech,
                "total": vals["total"],
                "satisfied": vals["satisfied"],
                "unsatisfied": vals["total"] - vals["satisfied"],
                "satisfaction_rate": round(vals["satisfied"] / vals["total"], 4) if vals["total"] > 0 else 0.0,
            }
            for tech, vals in sorted(by_technology.items(), key=lambda x: x[1]["total"], reverse=True)
        ]

        # Low-quality docs (satisfaction < 50% with at least 2 feedback entries)
        low_quality = [
            d for d in by_document
            if d["satisfaction_rate"] < 0.5 and d["total"] >= 2
        ]

        return {
            "total": total,
            "satisfied": satisfied,
            "unsatisfied": unsatisfied,
            "satisfaction_rate": rate,
            "by_technology": tech_stats,
            "by_document": by_document,
            "low_quality_docs": low_quality,
        }

    @staticmethod
    def doc_uri(technology: str, rel_path: str) -> str:
        return f"doc://{technology}/{rel_path}"

    def _read_manifest(self, tech_dir: Path) -> dict[str, Any]:
        manifest_path = tech_dir / "manifest.json"
        if not manifest_path.exists():
            payload = {"technology": tech_dir.name}
        else:
            payload = read_json(manifest_path)
            payload.setdefault("technology", tech_dir.name)

        # Auto-extract version from content if manifest doesn't have it
        if not payload.get("version"):
            payload["version"] = self._infer_version(tech_dir)
        return payload

    @staticmethod
    def _infer_version(tech_dir: Path) -> str | None:
        """Try to extract a version string from llms.txt header or doc content."""
        import re
        # Check llms.txt / llms-full.txt first lines for version patterns
        for name in ("llms-full.txt", "llms.txt"):
            candidate = tech_dir / name
            if candidate.exists():
                try:
                    header = candidate.read_text(encoding="utf-8")[:2000]
                except OSError:
                    continue
                # Match patterns like "v4.2.1", "version 3.13", "Version: 2.1.0"
                m = re.search(
                    r'(?:version[:\s]*|v)(\d+\.\d+(?:\.\d+)?(?:[a-z]\d*)?)',
                    header, re.IGNORECASE,
                )
                if m:
                    return m.group(1)
        # Check docs subdirectory paths for version patterns (e.g. docs/v5.5.3/)
        docs_dir = tech_dir / "docs"
        if docs_dir.exists():
            for child in sorted(docs_dir.iterdir()):
                if child.is_dir():
                    m = re.match(r'v?(\d+\.\d+(?:\.\d+)?)', child.name)
                    if m:
                        return m.group(1)
        return None

    def _collect_documents(
        self, tech_dir: Path, technology: str, version: str | None
    ) -> list[DocumentRecord]:
        docs: list[DocumentRecord] = []
        for path in sorted(tech_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name == "manifest.json" or path.name.startswith("."):
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            content = path.read_text(encoding="utf-8")
            rel_path = path.relative_to(tech_dir).as_posix()
            docs.append(
                DocumentRecord(
                    technology=technology,
                    rel_path=rel_path,
                    title=extract_title(path, content),
                    version=version,
                    checksum=sha256_text(content),
                    source_path=str(path),
                )
            )
        return docs

    @staticmethod
    def _build_snippet(content: str, query: str, radius: int = 100) -> str:
        lower = content.lower()
        idx = lower.find(query.lower())
        if idx < 0:
            return content[: radius * 2].strip()
        start = max(0, idx - radius)
        end = min(len(content), idx + len(query) + radius)
        return content[start:end].strip()
