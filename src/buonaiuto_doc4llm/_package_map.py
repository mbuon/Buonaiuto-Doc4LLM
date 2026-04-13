"""Shared package-name → technology-ID mapping.

This module has no imports from within the buonaiuto_doc4llm package so that
both auto_setup.py and manifest_parsers.py can import from it without creating
circular dependencies.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Static mapping: well-known package names → technology IDs.
# Keep entries here only for packages that are unlikely to publish their own
# llms.txt — or where the package name differs from the technology slug the
# docs_center uses.  Everything else is discovered at runtime via the registry
# or the URL-probe mechanism.
# ---------------------------------------------------------------------------
PACKAGE_TO_TECHNOLOGY: dict[str, str] = {
    # React ecosystem
    "react": "react",
    "react-dom": "react",
    # Next.js
    "next": "nextjs",
    "nextjs": "nextjs",
    # Other frontend frameworks
    "svelte": "svelte",
    "vue": "vue",
    "angular": "angular",
    # Supabase
    "@supabase/supabase-js": "supabase",
    "supabase": "supabase",
    "supabase-py": "supabase",
    # Python backend
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "sqlalchemy": "sqlalchemy",
    "pytest": "pytest",
    # AI / LLM
    "langchain": "langchain",
    "llamaindex": "llamaindex",
    "openai": "openai",
    "anthropic": "anthropic",
    "@anthropic-ai/sdk": "anthropic",
    "transformers": "huggingface-transformers",
    # Infrastructure
    "docker": "docker",
    "kubernetes": "kubernetes",
    "terraform": "terraform",
    # Styling & build
    "tailwindcss": "tailwindcss",
    "vite": "vite",
    "typescript": "typescript",
    # Payments
    "stripe": "stripe",
    "@stripe/stripe-js": "stripe",
    "@stripe/react-stripe-js": "stripe",
    "@stripe/connect-js": "stripe",
}

# Config-file hints: presence of these files implies the technology.
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

# Source-file extension fallback: if no manifest is found, infer language/
# runtime from the file types present in the project tree.
EXTENSION_TO_TECHNOLOGY: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "dotnet",
    ".swift": "swift",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
}

# Ecosystem name for each manifest file type — used when recording observed
# packages into the DB so we know where the package name came from.
MANIFEST_ECOSYSTEM: dict[str, str] = {
    "package.json": "npm",
    "requirements.txt": "pypi",
    "pyproject.toml": "pypi",
    "setup.py": "pypi",
    "setup.cfg": "pypi",
    "Pipfile": "pypi",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "Gemfile": "gem",
    "composer.json": "composer",
    "pubspec.yaml": "pub",
    "*.csproj": "nuget",
    "packages.config": "nuget",
}


def map_package_to_technology(package_name: str) -> str | None:
    """Return the technology ID for *package_name*, or None if unknown.

    Checks the static PACKAGE_TO_TECHNOLOGY dict first, then falls back to
    the bundled ingestion registry (src/ingestion/registry.json).
    """
    normalized = package_name.strip().lower()
    if not normalized:
        return None
    normalized = re.split(r"[<>=!~\[]", normalized, maxsplit=1)[0].strip()
    mapped = PACKAGE_TO_TECHNOLOGY.get(normalized)
    if mapped is not None:
        return mapped
    return _registry_package_map().get(normalized)


def _registry_package_map() -> dict[str, str]:
    """Build package-name → technology-ID from the bundled ingestion registry.

    This keeps detection aligned with src/ingestion/registry.json so adding a
    new library entry there automatically improves auto-detection.
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
        for pkg in package_names:
            if isinstance(pkg, str) and pkg.strip():
                package_map[pkg.strip().lower()] = library_id.strip()
    return package_map
