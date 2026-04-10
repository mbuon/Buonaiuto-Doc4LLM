"""Split monolithic llms.txt / llms-full.txt files at h1 heading boundaries.

Produces individual .md files in a target directory, one per top-level section.
This makes each section independently searchable by DocsHubService.scan().
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex matching a line that starts with exactly one '#' followed by a space.
# Does NOT match ## or ### (those are sub-sections, kept inside the parent).
_H1_RE = re.compile(r"^# ", re.MULTILINE)


def _slugify(heading: str) -> str:
    """Convert a heading string to a filename-safe slug.

    Lowercases, replaces non-alphanumeric runs with hyphens, strips
    leading/trailing hyphens, and truncates to 80 characters.
    """
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:80]


def split_monolith(
    source_path: Path,
    output_dir: Path,
    min_size_bytes: int = 100_000,
) -> list[Path]:
    """Split a large text file at h1 heading boundaries.

    Args:
        source_path: Path to the monolith text file.
        output_dir: Directory where individual .md files will be written.
        min_size_bytes: Files smaller than this are not split (default 100KB).

    Returns:
        List of created file paths.  Returns an empty list if the file is
        under *min_size_bytes* or contains fewer than two h1 headings.
    """
    if not source_path.exists():
        return []

    size = source_path.stat().st_size
    if size < min_size_bytes:
        return []

    content = source_path.read_text(encoding="utf-8")

    # Split at h1 boundaries while keeping the heading with its content.
    # re.split with a lookahead keeps the delimiter attached to the next chunk.
    sections = re.split(r"(?=^# )", content, flags=re.MULTILINE)
    # Filter out any empty/whitespace-only leading chunk (text before first h1).
    sections = [s for s in sections if s.strip()]

    # Only sections that actually start with '# ' are h1 sections.
    h1_sections = [s for s in sections if s.startswith("# ")]

    if len(h1_sections) < 2:
        # Nothing to split — single section or no h1 headings.
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    slug_counts: dict[str, int] = {}

    for section in h1_sections:
        # Extract heading text from the first line.
        first_line = section.split("\n", 1)[0]
        heading_text = first_line.lstrip("# ").strip()
        slug = _slugify(heading_text)
        if not slug:
            slug = "section"

        # Handle duplicate slugs with numeric suffixes.
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        count = slug_counts[slug]
        if count > 1:
            filename = f"{slug}-{count}.md"
        else:
            filename = f"{slug}.md"

        dest = output_dir / filename
        dest.write_text(section, encoding="utf-8")
        created.append(dest)

    logger.info(
        "Split %s into %d sections in %s",
        source_path.name,
        len(created),
        output_dir,
    )
    return created
