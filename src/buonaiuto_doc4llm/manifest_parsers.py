"""Manifest parsers for project technology detection.

Each ``_detect_from_*`` function accepts a project root Path and returns a
``set[str]`` of resolved technology IDs (via ``map_package_to_technology``).

``collect_all_packages`` runs every parser and returns the raw package names
with their ecosystems — used to populate the ``observed_packages`` DB table so
the server can later try to discover llms.txt URLs for unknown packages.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from buonaiuto_doc4llm._package_map import EXTENSION_TO_TECHNOLOGY, map_package_to_technology

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — Python < 3.11
    tomllib = None  # type: ignore[assignment]

# Directories to skip during file-extension fallback scan.
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    ".tox", "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".cargo", "site-packages",
}


# ---------------------------------------------------------------------------
# Python ecosystem parsers
# ---------------------------------------------------------------------------

def _detect_from_setup_py(root: Path) -> set[str]:
    setup_py = root / "setup.py"
    if not setup_py.exists():
        return set()
    text = setup_py.read_text(encoding="utf-8", errors="replace")
    # Extract the list literal after install_requires=[ ... ]
    m = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        return set()
    technologies: set[str] = set()
    for pkg in re.findall(r"['\"]([A-Za-z0-9_.\-]+)", m.group(1)):
        t = map_package_to_technology(pkg)
        if t:
            technologies.add(t)
    return technologies


def _packages_from_setup_py(root: Path) -> list[dict[str, str]]:
    setup_py = root / "setup.py"
    if not setup_py.exists():
        return []
    text = setup_py.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if not m:
        return []
    return [
        {"name": pkg, "ecosystem": "pypi"}
        for pkg in re.findall(r"['\"]([A-Za-z0-9_.\-]+)", m.group(1))
    ]


def _detect_from_setup_cfg(root: Path) -> set[str]:
    setup_cfg = root / "setup.cfg"
    if not setup_cfg.exists():
        return set()
    import configparser
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(setup_cfg.read_text(encoding="utf-8", errors="replace"))
    except configparser.Error:
        return set()
    raw = cfg.get("options", "install_requires", fallback="")
    technologies: set[str] = set()
    for line in raw.splitlines():
        pkg = re.split(r"[<>=!~\[;\s]", line.strip(), maxsplit=1)[0].strip()
        if pkg:
            t = map_package_to_technology(pkg)
            if t:
                technologies.add(t)
    return technologies


def _packages_from_setup_cfg(root: Path) -> list[dict[str, str]]:
    setup_cfg = root / "setup.cfg"
    if not setup_cfg.exists():
        return []
    import configparser
    cfg = configparser.ConfigParser()
    try:
        cfg.read_string(setup_cfg.read_text(encoding="utf-8", errors="replace"))
    except configparser.Error:
        return []
    raw = cfg.get("options", "install_requires", fallback="")
    pkgs = []
    for line in raw.splitlines():
        pkg = re.split(r"[<>=!~\[;\s]", line.strip(), maxsplit=1)[0].strip()
        if pkg:
            pkgs.append({"name": pkg, "ecosystem": "pypi"})
    return pkgs


def _detect_from_pipfile(root: Path) -> set[str]:
    pipfile = root / "Pipfile"
    if not pipfile.exists() or tomllib is None:
        return set()
    try:
        data = tomllib.loads(pipfile.read_text(encoding="utf-8"))
    except Exception:
        return set()
    technologies: set[str] = set()
    for section in ("packages", "dev-packages"):
        for pkg in data.get(section, {}):
            t = map_package_to_technology(str(pkg))
            if t:
                technologies.add(t)
    return technologies


def _packages_from_pipfile(root: Path) -> list[dict[str, str]]:
    pipfile = root / "Pipfile"
    if not pipfile.exists() or tomllib is None:
        return []
    try:
        data = tomllib.loads(pipfile.read_text(encoding="utf-8"))
    except Exception:
        return []
    pkgs = []
    for section in ("packages", "dev-packages"):
        for pkg in data.get(section, {}):
            pkgs.append({"name": str(pkg), "ecosystem": "pypi"})
    return pkgs


# ---------------------------------------------------------------------------
# Rust / Cargo
# ---------------------------------------------------------------------------

def _detect_from_cargo_toml(root: Path) -> set[str]:
    cargo = root / "Cargo.toml"
    if not cargo.exists() or tomllib is None:
        return set()
    try:
        data = tomllib.loads(cargo.read_text(encoding="utf-8"))
    except Exception:
        return set()
    technologies: set[str] = set()
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for crate in data.get(section, {}):
            t = map_package_to_technology(str(crate))
            if t:
                technologies.add(t)
    return technologies


def _packages_from_cargo_toml(root: Path) -> list[dict[str, str]]:
    cargo = root / "Cargo.toml"
    if not cargo.exists() or tomllib is None:
        return []
    try:
        data = tomllib.loads(cargo.read_text(encoding="utf-8"))
    except Exception:
        return []
    pkgs = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for crate in data.get(section, {}):
            pkgs.append({"name": str(crate), "ecosystem": "cargo"})
    return pkgs


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

def _detect_from_go_mod(root: Path) -> set[str]:
    go_mod = root / "go.mod"
    if not go_mod.exists():
        return set()
    text = go_mod.read_text(encoding="utf-8", errors="replace")
    technologies: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("module") or line.startswith("go "):
            continue
        # require lines: \tgithub.com/foo/bar v1.2.3
        m = re.match(r"([\w./\-]+)\s+v[\d.]", line.lstrip("require (").strip())
        if m:
            module_path = m.group(1)
            # Use the last segment as a hint
            slug = module_path.rstrip("/").split("/")[-1]
            t = map_package_to_technology(slug)
            if t:
                technologies.add(t)
    return technologies


def _packages_from_go_mod(root: Path) -> list[dict[str, str]]:
    go_mod = root / "go.mod"
    if not go_mod.exists():
        return []
    text = go_mod.read_text(encoding="utf-8", errors="replace")
    pkgs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("module") or line.startswith("go "):
            continue
        m = re.match(r"([\w./\-]+)\s+v[\d.]", line.lstrip("require (").strip())
        if m:
            pkgs.append({"name": m.group(1), "ecosystem": "go"})
    return pkgs


# ---------------------------------------------------------------------------
# Java / Kotlin — Maven
# ---------------------------------------------------------------------------

def _detect_from_pom_xml(root: Path) -> set[str]:
    pom = root / "pom.xml"
    if not pom.exists():
        return set()
    try:
        tree = ET.parse(pom)
    except ET.ParseError:
        return set()
    ns_match = re.match(r"\{([^}]+)\}", tree.getroot().tag)
    ns = f"{{{ns_match.group(1)}}}" if ns_match else ""
    technologies: set[str] = set()
    for dep in tree.findall(f".//{ns}dependency"):
        artifact = dep.findtext(f"{ns}artifactId") or ""
        t = map_package_to_technology(artifact.strip())
        if t:
            technologies.add(t)
    return technologies


def _packages_from_pom_xml(root: Path) -> list[dict[str, str]]:
    pom = root / "pom.xml"
    if not pom.exists():
        return []
    try:
        tree = ET.parse(pom)
    except ET.ParseError:
        return []
    ns_match = re.match(r"\{([^}]+)\}", tree.getroot().tag)
    ns = f"{{{ns_match.group(1)}}}" if ns_match else ""
    pkgs = []
    for dep in tree.findall(f".//{ns}dependency"):
        group = dep.findtext(f"{ns}groupId") or ""
        artifact = dep.findtext(f"{ns}artifactId") or ""
        if artifact.strip():
            name = f"{group.strip()}:{artifact.strip()}" if group.strip() else artifact.strip()
            pkgs.append({"name": name, "ecosystem": "maven"})
    return pkgs


# ---------------------------------------------------------------------------
# Java / Kotlin — Gradle
# ---------------------------------------------------------------------------

def _detect_from_build_gradle(root: Path) -> set[str]:
    for filename in ("build.gradle", "build.gradle.kts"):
        gradle = root / filename
        if not gradle.exists():
            continue
        text = gradle.read_text(encoding="utf-8", errors="replace")
        technologies: set[str] = set()
        for m in re.finditer(
            r"""(?:implementation|api|compile|testImplementation|runtimeOnly)\s*[\('"]([\w.:\-]+)""",
            text,
        ):
            # coords like "com.squareup.okhttp3:okhttp:4.11" — use last colon-part
            slug = m.group(1).split(":")[-1] if ":" in m.group(1) else m.group(1)
            t = map_package_to_technology(slug)
            if t:
                technologies.add(t)
        return technologies
    return set()


def _packages_from_build_gradle(root: Path) -> list[dict[str, str]]:
    pkgs = []
    for filename in ("build.gradle", "build.gradle.kts"):
        gradle = root / filename
        if not gradle.exists():
            continue
        text = gradle.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r"""(?:implementation|api|compile|testImplementation|runtimeOnly)\s*[\('"]([\w.:\-]+)""",
            text,
        ):
            pkgs.append({"name": m.group(1), "ecosystem": "gradle"})
        break
    return pkgs


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

def _detect_from_gemfile(root: Path) -> set[str]:
    gemfile = root / "Gemfile"
    if not gemfile.exists():
        return set()
    text = gemfile.read_text(encoding="utf-8", errors="replace")
    technologies: set[str] = set()
    for m in re.finditer(r"""gem\s+['"]([A-Za-z0-9_\-]+)['"]""", text):
        t = map_package_to_technology(m.group(1))
        if t:
            technologies.add(t)
    return technologies


def _packages_from_gemfile(root: Path) -> list[dict[str, str]]:
    gemfile = root / "Gemfile"
    if not gemfile.exists():
        return []
    text = gemfile.read_text(encoding="utf-8", errors="replace")
    return [
        {"name": m.group(1), "ecosystem": "gem"}
        for m in re.finditer(r"""gem\s+['"]([A-Za-z0-9_\-]+)['"]""", text)
    ]


# ---------------------------------------------------------------------------
# PHP / Composer
# ---------------------------------------------------------------------------

def _detect_from_composer_json(root: Path) -> set[str]:
    composer = root / "composer.json"
    if not composer.exists():
        return set()
    try:
        import json
        data = json.loads(composer.read_text(encoding="utf-8"))
    except Exception:
        return set()
    technologies: set[str] = set()
    for section in ("require", "require-dev"):
        for pkg in data.get(section, {}):
            # vendor/package → use package part
            slug = str(pkg).split("/")[-1]
            t = map_package_to_technology(slug)
            if t:
                technologies.add(t)
    return technologies


def _packages_from_composer_json(root: Path) -> list[dict[str, str]]:
    composer = root / "composer.json"
    if not composer.exists():
        return []
    try:
        import json
        data = json.loads(composer.read_text(encoding="utf-8"))
    except Exception:
        return []
    pkgs = []
    for section in ("require", "require-dev"):
        for pkg in data.get(section, {}):
            pkgs.append({"name": str(pkg), "ecosystem": "composer"})
    return pkgs


# ---------------------------------------------------------------------------
# Dart / Flutter
# ---------------------------------------------------------------------------

def _detect_from_pubspec_yaml(root: Path) -> set[str]:
    pubspec = root / "pubspec.yaml"
    if not pubspec.exists():
        return set()
    text = pubspec.read_text(encoding="utf-8", errors="replace")
    technologies: set[str] = set()
    # Simple line parser — avoids a PyYAML hard dependency.
    in_deps = False
    for line in text.splitlines():
        if re.match(r"^(dependencies|dev_dependencies)\s*:", line):
            in_deps = True
            continue
        if in_deps:
            if line and not line[0].isspace():
                in_deps = False
                continue
            m = re.match(r"\s{2,4}([A-Za-z0-9_\-]+)\s*:", line)
            if m and m.group(1) != "sdk":
                t = map_package_to_technology(m.group(1))
                if t:
                    technologies.add(t)
    return technologies


def _packages_from_pubspec_yaml(root: Path) -> list[dict[str, str]]:
    pubspec = root / "pubspec.yaml"
    if not pubspec.exists():
        return []
    text = pubspec.read_text(encoding="utf-8", errors="replace")
    pkgs = []
    in_deps = False
    for line in text.splitlines():
        if re.match(r"^(dependencies|dev_dependencies)\s*:", line):
            in_deps = True
            continue
        if in_deps:
            if line and not line[0].isspace():
                in_deps = False
                continue
            m = re.match(r"\s{2,4}([A-Za-z0-9_\-]+)\s*:", line)
            if m and m.group(1) != "sdk":
                pkgs.append({"name": m.group(1), "ecosystem": "pub"})
    return pkgs


# ---------------------------------------------------------------------------
# .NET / C#
# ---------------------------------------------------------------------------

def _detect_from_csproj(root: Path) -> set[str]:
    technologies: set[str] = set()
    for csproj in list(root.glob("*.csproj")) + list(root.glob("**/*.csproj")):
        try:
            tree = ET.parse(csproj)
        except ET.ParseError:
            continue
        for ref in tree.findall(".//PackageReference"):
            pkg = ref.get("Include", "")
            t = map_package_to_technology(pkg.strip())
            if t:
                technologies.add(t)
    packages_config = root / "packages.config"
    if packages_config.exists():
        try:
            tree = ET.parse(packages_config)
            for pkg in tree.findall(".//package"):
                name = pkg.get("id", "")
                t = map_package_to_technology(name.strip())
                if t:
                    technologies.add(t)
        except ET.ParseError:
            pass
    return technologies


def _packages_from_csproj(root: Path) -> list[dict[str, str]]:
    pkgs = []
    for csproj in list(root.glob("*.csproj")) + list(root.glob("**/*.csproj")):
        try:
            tree = ET.parse(csproj)
        except ET.ParseError:
            continue
        for ref in tree.findall(".//PackageReference"):
            pkg = ref.get("Include", "")
            if pkg.strip():
                pkgs.append({"name": pkg.strip(), "ecosystem": "nuget"})
    packages_config = root / "packages.config"
    if packages_config.exists():
        try:
            tree = ET.parse(packages_config)
            for pkg in tree.findall(".//package"):
                name = pkg.get("id", "")
                if name.strip():
                    pkgs.append({"name": name.strip(), "ecosystem": "nuget"})
        except ET.ParseError:
            pass
    return pkgs


# ---------------------------------------------------------------------------
# File-extension fallback (Layer 5: no manifest present)
# ---------------------------------------------------------------------------

def _detect_from_file_extensions(root: Path) -> set[str]:
    """Infer technologies from source file extensions.

    Walks the project tree, skipping common non-source directories.  Only
    activates for extensions defined in EXTENSION_TO_TECHNOLOGY.
    """
    found_extensions: set[str] = set()
    try:
        for path in root.rglob("*"):
            if any(skip in path.parts for skip in _SKIP_DIRS):
                continue
            if path.is_file():
                found_extensions.add(path.suffix.lower())
    except PermissionError:
        pass
    return {
        EXTENSION_TO_TECHNOLOGY[ext]
        for ext in found_extensions
        if ext in EXTENSION_TO_TECHNOLOGY
    }


# ---------------------------------------------------------------------------
# Public API: collect ALL raw package names across every ecosystem
# ---------------------------------------------------------------------------

def collect_all_packages(root: Path | str) -> list[dict[str, Any]]:
    """Return every raw package name found in the project, with its ecosystem.

    Unlike ``detect_project_technologies``, this does NOT map names to
    technology IDs — it returns the original package names so the server can
    persist them and later attempt to discover llms.txt URLs for unknown ones.

    Each entry: ``{"name": str, "ecosystem": str}``.
    Duplicates within the same ecosystem are removed; order is not guaranteed.
    """
    r = Path(root)
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []

    def _add(pkgs: list[dict[str, str]]) -> None:
        for p in pkgs:
            key = (p["name"].strip().lower(), p["ecosystem"])
            if key not in seen:
                seen.add(key)
                result.append({"name": p["name"].strip(), "ecosystem": p["ecosystem"]})

    # npm
    _add(_packages_from_package_json(r))
    # pypi
    _add(_packages_from_requirements_txt(r))
    _add(_packages_from_pyproject_toml(r))
    _add(_packages_from_setup_py(r))
    _add(_packages_from_setup_cfg(r))
    _add(_packages_from_pipfile(r))
    # cargo
    _add(_packages_from_cargo_toml(r))
    # go
    _add(_packages_from_go_mod(r))
    # maven / gradle
    _add(_packages_from_pom_xml(r))
    _add(_packages_from_build_gradle(r))
    # ruby
    _add(_packages_from_gemfile(r))
    # php
    _add(_packages_from_composer_json(r))
    # dart
    _add(_packages_from_pubspec_yaml(r))
    # .net
    _add(_packages_from_csproj(r))

    return result


# ---------------------------------------------------------------------------
# npm / package.json (mirrors logic already in auto_setup._detect_from_package_json)
# ---------------------------------------------------------------------------

def _packages_from_package_json(root: Path) -> list[dict[str, str]]:
    import json as _json
    package_json = root / "package.json"
    if not package_json.exists():
        return []
    try:
        payload = _json.loads(package_json.read_text(encoding="utf-8"))
    except _json.JSONDecodeError:
        return []
    pkgs = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name in payload.get(section, {}):
            pkgs.append({"name": str(name), "ecosystem": "npm"})
    return pkgs


# ---------------------------------------------------------------------------
# pypi helpers (mirrors auto_setup._detect_from_requirements / _detect_from_pyproject)
# ---------------------------------------------------------------------------

def _packages_from_requirements_txt(root: Path) -> list[dict[str, str]]:
    req_file = root / "requirements.txt"
    if not req_file.exists():
        return []
    pkgs = []
    for raw_line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        pkg = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].strip()
        if pkg:
            pkgs.append({"name": pkg, "ecosystem": "pypi"})
    return pkgs


def _packages_from_pyproject_toml(root: Path) -> list[dict[str, str]]:
    pyproject = root / "pyproject.toml"
    if tomllib is None or not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return []
    pkgs = []
    project = data.get("project", {})
    for dep in project.get("dependencies", []) if isinstance(project, dict) else []:
        pkg = re.split(r"[<>=!~\[;\s]", str(dep), maxsplit=1)[0].strip()
        if pkg:
            pkgs.append({"name": pkg, "ecosystem": "pypi"})
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    for values in optional.values() if isinstance(optional, dict) else []:
        for dep in values if isinstance(values, list) else []:
            pkg = re.split(r"[<>=!~\[;\s]", str(dep), maxsplit=1)[0].strip()
            if pkg:
                pkgs.append({"name": pkg, "ecosystem": "pypi"})
    return pkgs


# ---------------------------------------------------------------------------
# Expose all detect functions for use in auto_setup.detect_project_technologies
# ---------------------------------------------------------------------------

__all__ = [
    "collect_all_packages",
    "_detect_from_setup_py",
    "_detect_from_setup_cfg",
    "_detect_from_pipfile",
    "_detect_from_cargo_toml",
    "_detect_from_go_mod",
    "_detect_from_pom_xml",
    "_detect_from_build_gradle",
    "_detect_from_gemfile",
    "_detect_from_composer_json",
    "_detect_from_pubspec_yaml",
    "_detect_from_csproj",
    "_detect_from_file_extensions",
]
