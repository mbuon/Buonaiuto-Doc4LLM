from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LibraryMapping:
    library_id: str
    package_names: list[str]
    sources: list[str]


class CanonicalSourceMapper:
    def __init__(self, mappings: list[LibraryMapping]):
        self._mappings = mappings
        self._by_package: dict[str, LibraryMapping] = {}
        for mapping in mappings:
            for package_name in mapping.package_names:
                normalized = package_name.strip().lower()
                if normalized:
                    self._by_package[normalized] = mapping

    def resolve_by_package(self, package_name: str) -> LibraryMapping | None:
        return self._by_package.get(package_name.strip().lower())

    @staticmethod
    def preferred_source(sources: list[str]) -> str | None:
        ordered = CanonicalSourceMapper.ordered_sources(sources)
        return ordered[0] if ordered else None

    @staticmethod
    def ordered_sources(sources: list[str]) -> list[str]:
        """Return sources ordered by priority: llms-full.txt first, llms.txt second, github:// last."""
        if not sources:
            return []
        full: list[str] = []
        llms: list[str] = []
        github: list[str] = []
        rest: list[str] = []
        for source in sources:
            s = source.strip()
            if s.startswith("github://"):
                github.append(s)
                continue
            path = urlsplit(s).path.lower()
            if path.endswith("/llms-full.txt"):
                full.append(s)
            elif path.endswith("/llms.txt"):
                llms.append(s)
            else:
                rest.append(s)
        return full + llms + rest + github
