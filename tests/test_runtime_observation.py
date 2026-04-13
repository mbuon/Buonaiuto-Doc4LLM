"""Tests for runtime technology observation: manifest parsers, observed_packages DB,
_probe_llms_txt, resolve_observed_packages, and MCP tool surface."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from buonaiuto_doc4llm.auto_setup import detect_project_technologies
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
from buonaiuto_doc4llm.service import DocsHubService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_path: Path) -> DocsHubService:
    return DocsHubService(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Group 1: New manifest parsers
# ---------------------------------------------------------------------------

class TestSetupPyParser:
    def test_detects_known_package(self, tmp_path: Path) -> None:
        (tmp_path / "setup.py").write_text(
            "from setuptools import setup\nsetup(install_requires=['fastapi', 'uvicorn'])\n"
        )
        assert "fastapi" in _detect_from_setup_py(tmp_path)

    def test_unknown_package_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "setup.py").write_text("setup(install_requires=['totally-unknown-lib'])\n")
        result = _detect_from_setup_py(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_setup_py(tmp_path) == set()


class TestSetupCfgParser:
    def test_detects_known_package(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text(
            "[options]\ninstall_requires =\n    fastapi\n    uvicorn\n"
        )
        assert "fastapi" in _detect_from_setup_cfg(tmp_path)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_setup_cfg(tmp_path) == set()


class TestPipfileParser:
    def test_detects_known_package(self, tmp_path: Path) -> None:
        (tmp_path / "Pipfile").write_text(
            '[packages]\nfastapi = "*"\nrequests = "*"\n'
        )
        assert "fastapi" in _detect_from_pipfile(tmp_path)

    def test_unknown_packages_do_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "Pipfile").write_text('[packages]\nunknown-lib = "*"\n')
        result = _detect_from_pipfile(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_pipfile(tmp_path) == set()


class TestCargoTomlParser:
    def test_unknown_crate_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1.0"\n'
        )
        result = _detect_from_cargo_toml(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_cargo_toml(tmp_path) == set()


class TestGoModParser:
    def test_does_not_raise_on_valid_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text(
            "module myapp\n\ngo 1.21\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.0\n)\n"
        )
        result = _detect_from_go_mod(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_go_mod(tmp_path) == set()


class TestPomXmlParser:
    def test_does_not_raise_on_valid_pom(self, tmp_path: Path) -> None:
        (tmp_path / "pom.xml").write_text(
            '<?xml version="1.0"?><project xmlns="http://maven.apache.org/POM/4.0.0">'
            "<dependencies><dependency>"
            "<groupId>org.springframework.boot</groupId>"
            "<artifactId>spring-boot</artifactId>"
            "</dependency></dependencies></project>"
        )
        result = _detect_from_pom_xml(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_pom_xml(tmp_path) == set()


class TestBuildGradleParser:
    def test_does_not_raise_on_valid_gradle(self, tmp_path: Path) -> None:
        (tmp_path / "build.gradle").write_text(
            "dependencies {\n    implementation 'com.squareup.okhttp3:okhttp:4.11.0'\n}\n"
        )
        result = _detect_from_build_gradle(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_build_gradle(tmp_path) == set()


class TestGemfileParser:
    def test_does_not_raise_on_valid_gemfile(self, tmp_path: Path) -> None:
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n")
        result = _detect_from_gemfile(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_gemfile(tmp_path) == set()


class TestComposerJsonParser:
    def test_does_not_raise_on_valid_composer(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text(
            json.dumps({"require": {"laravel/framework": "^10.0"}})
        )
        result = _detect_from_composer_json(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_composer_json(tmp_path) == set()


class TestPubspecYamlParser:
    def test_does_not_raise_on_valid_pubspec(self, tmp_path: Path) -> None:
        (tmp_path / "pubspec.yaml").write_text(
            "name: myapp\ndependencies:\n  flutter:\n    sdk: flutter\n  http: ^0.13.0\n"
        )
        result = _detect_from_pubspec_yaml(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_pubspec_yaml(tmp_path) == set()


class TestCsprojParser:
    def test_does_not_raise_on_valid_csproj(self, tmp_path: Path) -> None:
        (tmp_path / "MyApp.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk">'
            '<ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13.0.3" /></ItemGroup>'
            "</Project>"
        )
        result = _detect_from_csproj(tmp_path)
        assert isinstance(result, set)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _detect_from_csproj(tmp_path) == set()


class TestFileExtensionFallback:
    def test_python_files_detected(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        result = _detect_from_file_extensions(tmp_path)
        assert "python" in result

    def test_go_files_detected(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text("package main")
        result = _detect_from_file_extensions(tmp_path)
        assert "go" in result

    def test_rust_files_detected(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("fn main() {}")
        result = _detect_from_file_extensions(tmp_path)
        assert "rust" in result

    def test_typescript_files_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("const x: number = 1;")
        result = _detect_from_file_extensions(tmp_path)
        assert "typescript" in result

    def test_multiple_languages_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("const x = 1;")
        (tmp_path / "server.py").write_text("print('hi')")
        result = _detect_from_file_extensions(tmp_path)
        assert "typescript" in result
        assert "python" in result

    def test_node_modules_skipped(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "some-lib"
        nm.mkdir(parents=True)
        (nm / "index.go").write_text("package main")
        # No .go outside node_modules
        result = _detect_from_file_extensions(tmp_path)
        assert "go" not in result

    def test_additive_with_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
        (tmp_path / "main.go").write_text("package main")
        result = detect_project_technologies(tmp_path)
        assert "react" in result
        assert "go" in result


# ---------------------------------------------------------------------------
# Group 2: collect_all_packages
# ---------------------------------------------------------------------------

class TestCollectAllPackages:
    def test_npm_packages_collected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^18", "framer-motion": "^10"}})
        )
        pkgs = collect_all_packages(tmp_path)
        names = [p["name"] for p in pkgs]
        assert "react" in names
        assert "framer-motion" in names

    def test_ecosystem_tagged_correctly(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
        (tmp_path / "requirements.txt").write_text("fastapi==0.100.0\n")
        pkgs = collect_all_packages(tmp_path)
        ecosystems = {p["name"]: p["ecosystem"] for p in pkgs}
        assert ecosystems["react"] == "npm"
        assert ecosystems["fastapi"] == "pypi"

    def test_deduplicates_same_package(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"react": "^18"}, "devDependencies": {"react": "^18"}})
        )
        pkgs = collect_all_packages(tmp_path)
        npm_react = [p for p in pkgs if p["name"] == "react" and p["ecosystem"] == "npm"]
        assert len(npm_react) == 1

    def test_empty_project_returns_empty_list(self, tmp_path: Path) -> None:
        assert collect_all_packages(tmp_path) == []


# ---------------------------------------------------------------------------
# Group 3: observe_packages service method
# ---------------------------------------------------------------------------

class TestObservePackages:
    def test_upserts_into_db(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        svc.observe_packages(
            "myproject",
            [{"name": "framer-motion", "ecosystem": "npm"}, {"name": "httpx", "ecosystem": "pypi"}],
        )
        with svc._connect() as conn:
            rows = conn.execute("SELECT package_name, ecosystem FROM observed_packages ORDER BY package_name").fetchall()
        names = [(r["package_name"], r["ecosystem"]) for r in rows]
        assert ("framer-motion", "npm") in names
        assert ("httpx", "pypi") in names

    def test_idempotent_first_seen_at_unchanged(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        svc.observe_packages("p1", [{"name": "lodash", "ecosystem": "npm"}])
        with svc._connect() as conn:
            ts1 = conn.execute(
                "SELECT first_seen_at FROM observed_packages WHERE package_name = 'lodash'"
            ).fetchone()["first_seen_at"]
        svc.observe_packages("p1", [{"name": "lodash", "ecosystem": "npm"}])
        with svc._connect() as conn:
            ts2 = conn.execute(
                "SELECT first_seen_at FROM observed_packages WHERE package_name = 'lodash'"
            ).fetchone()["first_seen_at"]
        assert ts1 == ts2

    def test_stores_project_id(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        svc.observe_packages("alpha", [{"name": "axios", "ecosystem": "npm"}])
        with svc._connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM observed_packages WHERE package_name = 'axios'"
            ).fetchone()
        assert row["project_id"] == "alpha"

    def test_null_project_id_allowed(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        svc.observe_packages(None, [{"name": "some-lib", "ecosystem": "npm"}])
        with svc._connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM observed_packages WHERE package_name = 'some-lib'"
            ).fetchone()
        assert row["project_id"] is None

    def test_resolved_fields_initially_null(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        svc.observe_packages("p", [{"name": "newlib", "ecosystem": "npm"}])
        with svc._connect() as conn:
            row = conn.execute(
                "SELECT resolved_technology, resolved_at FROM observed_packages WHERE package_name = 'newlib'"
            ).fetchone()
        assert row["resolved_technology"] is None
        assert row["resolved_at"] is None

    def test_returns_summary(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        result = svc.observe_packages("p", [{"name": "x", "ecosystem": "npm"}])
        assert result["observed"] == 1
        assert result["project_id"] == "p"


# ---------------------------------------------------------------------------
# Group 4: install_project wires observe_packages
# ---------------------------------------------------------------------------

class TestInstallProjectObservesPackages:
    def test_records_unresolved_npm_package(self, tmp_path: Path) -> None:
        project_root = tmp_path / "myapp"
        project_root.mkdir()
        (project_root / "package.json").write_text(
            json.dumps({"dependencies": {"framer-motion": "^10.0"}})
        )
        svc = _make_service(tmp_path)
        svc.install_project(project_root=project_root, project_id="myapp")
        with svc._connect() as conn:
            row = conn.execute(
                "SELECT ecosystem FROM observed_packages WHERE package_name = 'framer-motion'"
            ).fetchone()
        assert row is not None
        assert row["ecosystem"] == "npm"

    def test_records_packages_from_multiple_ecosystems(self, tmp_path: Path) -> None:
        project_root = tmp_path / "myapp"
        project_root.mkdir()
        (project_root / "package.json").write_text(
            json.dumps({"dependencies": {"some-npm-lib": "^1.0"}})
        )
        (project_root / "requirements.txt").write_text("some-pypi-lib==1.0\n")
        svc = _make_service(tmp_path)
        svc.install_project(project_root=project_root, project_id="myapp")
        with svc._connect() as conn:
            rows = conn.execute(
                "SELECT package_name, ecosystem FROM observed_packages"
            ).fetchall()
        ecosystems = {r["package_name"]: r["ecosystem"] for r in rows}
        assert ecosystems.get("some-npm-lib") == "npm"
        assert ecosystems.get("some-pypi-lib") == "pypi"


# ---------------------------------------------------------------------------
# Group 5: _probe_llms_txt
# ---------------------------------------------------------------------------

class TestProbeLlmsTxt:
    def test_returns_none_on_all_404(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc._probe_llms_txt("unknownlib", "npm")
        assert result is None

    def test_returns_url_on_200_with_markdown(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# unknownlib\n\nThis is documentation for unknownlib covering all features.\n"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc._probe_llms_txt("unknownlib", "npm")
        assert result is not None
        assert "llms" in result

    def test_rejects_html_response(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<!DOCTYPE html><html><body>not docs</body></html>"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc._probe_llms_txt("unknownlib", "npm")
        assert result is None

    def test_returns_none_when_requests_unavailable(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        with patch("buonaiuto_doc4llm.service.requests", None):
            result = svc._probe_llms_txt("anylib", "npm")
        assert result is None


# ---------------------------------------------------------------------------
# Group 6: resolve_observed_packages
# ---------------------------------------------------------------------------

class TestResolveObservedPackages:
    def _insert_observed(self, svc: DocsHubService, name: str, ecosystem: str = "npm") -> None:
        with svc._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO observed_packages (package_name, ecosystem, first_seen_at) VALUES (?, ?, datetime('now'))",
                (name, ecosystem),
            )

    def test_discovers_and_registers_technology(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        self._insert_observed(svc, "coollib")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# coollib\n\nThis is the documentation for coollib, covering all its features and APIs.\n"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc.resolve_observed_packages(limit=10)

        assert len(result["resolved"]) >= 1
        resolved_names = [r["package_name"] for r in result["resolved"]]
        assert "coollib" in resolved_names

    def test_marks_resolved_technology_in_db(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        self._insert_observed(svc, "coollib2")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# coollib2\n\nThis is the documentation for coollib2, a library for doing things.\n"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            svc.resolve_observed_packages(limit=10)

        with svc._connect() as conn:
            row = conn.execute(
                "SELECT resolved_technology FROM observed_packages WHERE package_name = 'coollib2'"
            ).fetchone()
        assert row["resolved_technology"] is not None

    def test_skips_recently_attempted(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        with svc._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO observed_packages "
                "(package_name, ecosystem, first_seen_at, resolve_attempted_at) "
                "VALUES ('recentlib', 'npm', datetime('now'), datetime('now'))",
            )
        result = svc.resolve_observed_packages(limit=10)
        skipped_names = [r["package_name"] for r in result.get("skipped_packages", [])]
        # The package should not appear in resolved or failed, and skipped count > 0
        resolved_names = [r["package_name"] for r in result["resolved"]]
        failed_names = [r["package_name"] for r in result["failed"]]
        assert "recentlib" not in resolved_names
        assert "recentlib" not in failed_names

    def test_marks_failed_and_updates_attempted_at(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        self._insert_observed(svc, "faillib")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc.resolve_observed_packages(limit=10)

        failed_names = [r["package_name"] for r in result["failed"]]
        assert "faillib" in failed_names

        with svc._connect() as conn:
            row = conn.execute(
                "SELECT resolve_attempted_at FROM observed_packages WHERE package_name = 'faillib'"
            ).fetchone()
        assert row["resolve_attempted_at"] is not None

    def test_respects_limit(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        for i in range(10):
            self._insert_observed(svc, f"lib{i:02d}")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("buonaiuto_doc4llm.service.requests") as mock_requests:
            mock_requests.get.return_value = mock_resp
            result = svc.resolve_observed_packages(limit=3)

        total_processed = len(result["resolved"]) + len(result["failed"])
        assert total_processed <= 3

    def test_returns_expected_keys(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        result = svc.resolve_observed_packages(limit=10)
        assert "resolved" in result
        assert "failed" in result
        assert "skipped" in result


# ---------------------------------------------------------------------------
# Group 7: MCP tool surface
# ---------------------------------------------------------------------------

class TestMCPToolResolveObservedPackages:
    def test_tool_listed(self, tmp_path: Path) -> None:
        from buonaiuto_doc4llm.mcp_server import MCPServer
        server = MCPServer(base_dir=tmp_path)
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = server.handle_request(req)
        tool_names = [t["name"] for t in resp["result"]["tools"]]
        assert "resolve_observed_packages" in tool_names

    def test_tool_callable_returns_expected_keys(self, tmp_path: Path) -> None:
        from buonaiuto_doc4llm.mcp_server import MCPServer
        server = MCPServer(base_dir=tmp_path)
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "resolve_observed_packages", "arguments": {"limit": 5}},
        }
        resp = server.handle_request(req)
        assert "result" in resp
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert "resolved" in payload
        assert "failed" in payload
        assert "skipped" in payload

    def test_scan_docs_triggers_resolve_as_side_effect(self, tmp_path: Path) -> None:
        from buonaiuto_doc4llm.mcp_server import MCPServer
        server = MCPServer(base_dir=tmp_path)
        called = []
        original = server.service.resolve_observed_packages

        def _spy(**kwargs):
            called.append(True)
            return original(**kwargs)

        server.service.resolve_observed_packages = _spy
        req = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "scan_docs", "arguments": {}}}
        server.handle_request(req)
        assert len(called) == 1
