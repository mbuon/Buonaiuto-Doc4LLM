from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


PACKAGE_TO_TECHNOLOGY: dict[str, str] = {
    "react": "react",
    "react-dom": "react",
    "next": "nextjs",
    "nextjs": "nextjs",
    "svelte": "svelte",
    "vue": "vue",
    "angular": "angular",
    "@supabase/supabase-js": "supabase",
    "supabase": "supabase",
    "supabase-py": "supabase",
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "sqlalchemy": "sqlalchemy",
    "pytest": "pytest",
    "langchain": "langchain",
    "llamaindex": "llamaindex",
    "openai": "openai",
    "anthropic": "anthropic",
    "transformers": "huggingface-transformers",
    "docker": "docker",
    "kubernetes": "kubernetes",
    "terraform": "terraform",
    "tailwindcss": "tailwindcss",
    "vite": "vite",
    "typescript": "typescript",
    "stripe": "stripe",
}

FILE_HINTS: dict[str, str] = {
    "next.config.js": "nextjs",
    "next.config.mjs": "nextjs",
    "tailwind.config.js": "tailwindcss",
    "tailwind.config.ts": "tailwindcss",
    "vite.config.ts": "vite",
    "vite.config.js": "vite",
    "supabase/config.toml": "supabase",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "terraform.tf": "terraform",
}


def detect_project_technologies(project_root: Path | str) -> list[str]:
    root = Path(project_root)
    if not root.exists():
        raise ValueError(f"project_root does not exist: {root}")

    technologies: set[str] = set()
    technologies.update(_detect_from_package_json(root))
    technologies.update(_detect_from_requirements(root))
    technologies.update(_detect_from_pyproject(root))
    technologies.update(_detect_from_file_hints(root))
    technologies.update(_detect_from_docs_center(root))
    return sorted(technologies)


def _detect_from_docs_center(root: Path) -> list[str]:
    """Detect technologies already present under docs_center/technologies/."""
    tech_dir = root / "docs_center" / "technologies"
    if not tech_dir.is_dir():
        return []
    return [
        d.name
        for d in sorted(tech_dir.iterdir())
        if d.is_dir()
    ]


def bootstrap_project(
    base_dir: Path | str,
    project_root: Path | str,
    project_id: str | None = None,
    seed_technologies_root: Path | str | None = None,
) -> dict[str, Any]:
    base = Path(base_dir)
    root = Path(project_root)
    detected = detect_project_technologies(root)
    resolved_project_id = _resolve_project_id(project_id, root)

    docs_center = base / "docs_center"
    projects_dir = docs_center / "projects"
    tech_dir = docs_center / "technologies"
    projects_dir.mkdir(parents=True, exist_ok=True)
    tech_dir.mkdir(parents=True, exist_ok=True)

    project_payload = {
        "project_id": resolved_project_id,
        "name": root.name,
        "technologies": detected,
    }
    project_file = projects_dir / f"{resolved_project_id}.json"
    project_file.write_text(json.dumps(project_payload, indent=2), encoding="utf-8")

    source_root = Path(seed_technologies_root) if seed_technologies_root is not None else _default_seed_root()
    copied: list[str] = []
    missing: list[str] = []
    for technology in detected:
        source = source_root / technology
        destination = tech_dir / technology
        if not source.exists():
            missing.append(technology)
            continue
        _sync_tree(source, destination)
        copied.append(technology)

    return {
        "project_id": resolved_project_id,
        "project_file": str(project_file),
        "project_root": str(root),
        "technologies_detected": detected,
        "copied": copied,
        "missing": missing,
        "seed_technologies_root": str(source_root),
    }


def _detect_from_package_json(root: Path) -> set[str]:
    package_json = root / "package.json"
    if not package_json.exists():
        return set()
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()

    technologies: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        values = payload.get(section, {})
        if not isinstance(values, dict):
            continue
        for package_name in values.keys():
            mapped = _map_package_to_technology(str(package_name))
            if mapped:
                technologies.add(mapped)
    return technologies


def _detect_from_requirements(root: Path) -> set[str]:
    req_file = root / "requirements.txt"
    if not req_file.exists():
        return set()
    technologies: set[str] = set()
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        mapped = _map_package_to_technology(normalized)
        if mapped:
            technologies.add(mapped)
    return technologies


def _detect_from_pyproject(root: Path) -> set[str]:
    pyproject = root / "pyproject.toml"
    if tomllib is None or not pyproject.exists():
        return set()
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return set()

    technologies: set[str] = set()
    project = payload.get("project", {})
    dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
    if isinstance(dependencies, list):
        for dep in dependencies:
            mapped = _map_package_to_technology(str(dep))
            if mapped:
                technologies.add(mapped)

    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    if isinstance(optional, dict):
        for values in optional.values():
            if not isinstance(values, list):
                continue
            for dep in values:
                mapped = _map_package_to_technology(str(dep))
                if mapped:
                    technologies.add(mapped)
    return technologies


def _detect_from_file_hints(root: Path) -> set[str]:
    technologies: set[str] = set()
    for relative, technology in FILE_HINTS.items():
        if (root / relative).exists():
            technologies.add(technology)
    return technologies


def _map_package_to_technology(package_name: str) -> str | None:
    normalized = package_name.strip().lower()
    if not normalized:
        return None
    normalized = re.split(r"[<>=!~\[]", normalized, maxsplit=1)[0].strip()
    mapped = PACKAGE_TO_TECHNOLOGY.get(normalized)
    if mapped is not None:
        return mapped
    return _registry_package_to_technology_map().get(normalized)


def _registry_package_to_technology_map() -> dict[str, str]:
    """Build package-name aliases from the bundled ingestion registry.

    This keeps technology detection aligned with ``src/ingestion/registry.json``
    so adding a new library there automatically improves auto-detection.
    """
    registry_path = Path(__file__).resolve().parents[1] / "ingestion" / "registry.json"
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    libraries = data.get("libraries")
    if not isinstance(libraries, list):
        return {}

    package_map: dict[str, str] = {}
    for entry in libraries:
        if not isinstance(entry, dict):
            continue
        library_id = entry.get("library_id")
        package_names = entry.get("package_names", [])
        if not isinstance(library_id, str) or not library_id.strip():
            continue
        if not isinstance(package_names, list):
            continue
        for package_name in package_names:
            if not isinstance(package_name, str):
                continue
            normalized = package_name.strip().lower()
            if not normalized:
                continue
            package_map[normalized] = library_id.strip()

    return package_map


def _resolve_project_id(project_id: str | None, project_root: Path) -> str:
    if project_id is not None and project_id.strip():
        return project_id.strip()
    stem = project_root.name.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-")
    if not normalized:
        raise ValueError("Could not derive project_id from project_root")
    return normalized


def _default_seed_root() -> Path:
    # src/buonaiuto_doc4llm/auto_setup.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2] / "docs_center" / "technologies"


def _sync_tree(source: Path, destination: Path) -> None:
    import tempfile
    # Copy to temp dir first, then atomically replace to avoid data loss on failure
    tmp = Path(tempfile.mkdtemp(dir=destination.parent))
    try:
        shutil.copytree(source, tmp / destination.name)
        if destination.exists():
            shutil.rmtree(destination)
        (tmp / destination.name).rename(destination)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
