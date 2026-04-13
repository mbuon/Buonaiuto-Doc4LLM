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

from buonaiuto_doc4llm._package_map import (
    FILE_HINTS,
    PACKAGE_TO_TECHNOLOGY,
    map_package_to_technology,
)
from buonaiuto_doc4llm.manifest_parsers import (
    _detect_from_build_gradle,
    _detect_from_cargo_toml,
    _detect_from_composer_json,
    _detect_from_csproj,
    _detect_from_file_extensions,
    _detect_from_gemfile,
    _detect_from_go_mod,
    _detect_from_pipfile,
    _detect_from_pom_xml,
    _detect_from_pubspec_yaml,
    _detect_from_setup_cfg,
    _detect_from_setup_py,
    collect_all_packages,
)


def detect_project_technologies(project_root: Path | str) -> list[str]:
    root = Path(project_root)
    if not root.exists():
        raise ValueError(f"project_root does not exist: {root}")

    technologies: set[str] = set()
    # Layer 1: standard manifest parsers (original three)
    technologies.update(_detect_from_package_json(root))
    technologies.update(_detect_from_requirements(root))
    technologies.update(_detect_from_pyproject(root))
    # Layer 2: additional manifest parsers
    technologies.update(_detect_from_setup_py(root))
    technologies.update(_detect_from_setup_cfg(root))
    technologies.update(_detect_from_pipfile(root))
    technologies.update(_detect_from_cargo_toml(root))
    technologies.update(_detect_from_go_mod(root))
    technologies.update(_detect_from_pom_xml(root))
    technologies.update(_detect_from_build_gradle(root))
    technologies.update(_detect_from_gemfile(root))
    technologies.update(_detect_from_composer_json(root))
    technologies.update(_detect_from_pubspec_yaml(root))
    technologies.update(_detect_from_csproj(root))
    # Layer 3: config file hints
    technologies.update(_detect_from_file_hints(root))
    # Layer 4: self-describing llms.txt files
    technologies.update(_detect_from_local_llms_txt(root))
    # Layer 5: already-indexed technologies
    technologies.update(_detect_from_docs_center(root))
    # Layer 6: file-extension fallback (no manifest present)
    technologies.update(_detect_from_file_extensions(root))
    return sorted(technologies)


def _detect_from_local_llms_txt(root: Path) -> list[str]:
    """Detect technologies from llms.txt / llms-full.txt files in the project tree.

    Rules:
    - A file named ``llms.txt`` or ``llms-full.txt`` directly at *root* gets the
      technology ID ``root.name`` (the project folder name).
    - The same files found inside a subdirectory (any depth) get the technology ID
      equal to their **immediate parent directory name**.  This allows structures like
      ``docs/django/llms-full.txt`` → technology ``django``.
    """
    found: dict[str, Path] = {}  # tech_id -> best file (llms-full.txt preferred)
    llms_names = {"llms-full.txt", "llms.txt"}

    for candidate in root.rglob("llms*.txt"):
        if candidate.name not in llms_names:
            continue
        # Determine technology ID
        if candidate.parent == root:
            tech_id = root.name.lower()
        else:
            tech_id = candidate.parent.name.lower()

        tech_id = re.sub(r"[^a-z0-9_-]", "-", tech_id).strip("-")
        if not tech_id:
            continue

        # Prefer llms-full.txt over llms.txt
        existing = found.get(tech_id)
        if existing is None or candidate.name == "llms-full.txt":
            found[tech_id] = candidate

    return sorted(found.keys())


def ingest_local_llms_files(
    project_root: Path | str,
    base_dir: Path | str,
) -> dict[str, Any]:
    """Copy llms.txt / llms-full.txt files found in *project_root* into the
    local docs center at ``base_dir/docs_center/technologies/<tech_id>/``.

    Returns a dict with:
      - ``ingested``: list of technology IDs successfully copied
      - ``errors``: list of ``{"technology": ..., "error": ...}`` dicts
    """
    root = Path(project_root)
    base = Path(base_dir)
    llms_names = {"llms-full.txt", "llms.txt"}

    # Build same mapping as _detect_from_local_llms_txt (prefer llms-full.txt)
    best: dict[str, Path] = {}
    for candidate in root.rglob("llms*.txt"):
        if candidate.name not in llms_names:
            continue
        tech_id = root.name.lower() if candidate.parent == root else candidate.parent.name.lower()
        tech_id = re.sub(r"[^a-z0-9_-]", "-", tech_id).strip("-")
        if not tech_id:
            continue
        existing = best.get(tech_id)
        if existing is None or candidate.name == "llms-full.txt":
            best[tech_id] = candidate

    ingested: list[str] = []
    errors: list[dict[str, str]] = []
    tech_root = base / "docs_center" / "technologies"

    for tech_id, src_file in best.items():
        try:
            dest_dir = tech_root / tech_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / src_file.name
            shutil.copy2(src_file, dest_file)
            ingested.append(tech_id)
        except Exception as exc:
            errors.append({"technology": tech_id, "error": str(exc)})

    return {"ingested": ingested, "errors": errors}


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
            mapped = map_package_to_technology(str(package_name))
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
        mapped = map_package_to_technology(normalized)
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
            mapped = map_package_to_technology(str(dep))
            if mapped:
                technologies.add(mapped)

    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    if isinstance(optional, dict):
        for values in optional.values():
            if not isinstance(values, list):
                continue
            for dep in values:
                mapped = map_package_to_technology(str(dep))
                if mapped:
                    technologies.add(mapped)
    return technologies


def _detect_from_file_hints(root: Path) -> set[str]:
    technologies: set[str] = set()
    for relative, technology in FILE_HINTS.items():
        if (root / relative).exists():
            technologies.add(technology)
    return technologies


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
    # Copy to temp dir first, then atomically replace.
    # Keep a backup of the original so we can restore on failure — deleting
    # destination before the rename succeeds would cause permanent data loss.
    tmp = Path(tempfile.mkdtemp(dir=destination.parent))
    backup: Path | None = None
    try:
        shutil.copytree(source, tmp / destination.name)
        if destination.exists():
            backup = destination.parent / (destination.name + ".bak")
            destination.rename(backup)
        (tmp / destination.name).rename(destination)
        # Rename succeeded — discard the backup
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        # Restore backup if the rename failed
        if backup is not None and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
