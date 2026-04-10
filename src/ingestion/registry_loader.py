"""Load the canonical library registry from a JSON file."""
from __future__ import annotations

import json
from pathlib import Path

from ingestion.source_mapper import LibraryMapping


def load_registry(path: Path) -> list[LibraryMapping]:
    """Parse registry.json and return a list of LibraryMapping objects.

    Raises:
        FileNotFoundError: if the registry file does not exist.
        ValueError: if the file cannot be parsed as valid registry JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"Registry file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse registry file {path}: {exc}") from exc

    libraries = data.get("libraries")
    if not isinstance(libraries, list):
        raise ValueError(f"registry file {path} must contain a 'libraries' list")

    mappings: list[LibraryMapping] = []
    for entry in libraries:
        library_id = entry.get("library_id", "").strip()
        package_names = entry.get("package_names", [])
        sources = entry.get("sources", [])
        if not library_id:
            raise ValueError(f"Entry missing 'library_id' in registry: {entry}")
        mappings.append(
            LibraryMapping(
                library_id=library_id,
                package_names=list(package_names),
                sources=list(sources),
            )
        )

    return mappings


def default_registry_path() -> Path:
    """Return the path to the bundled seed registry.json."""
    return Path(__file__).parent / "registry.json"
