"""Tests for the 'fetch' and 'watch-and-fetch' CLI subcommands."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from buonaiuto_doc4llm.__main__ import build_parser


def _make_hub(tmp_path: Path) -> Path:
    """Create a minimal hub directory structure."""
    (tmp_path / "docs_center" / "technologies").mkdir(parents=True)
    (tmp_path / "docs_center" / "projects").mkdir(parents=True)
    (tmp_path / "state").mkdir(parents=True)
    return tmp_path


class TestFetchSubcommand:
    def test_fetch_subcommand_recognized_by_parser(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "fetch"])
        assert args.command == "fetch"
        assert args.technology is None

    def test_fetch_subcommand_accepts_technology_filter(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "fetch", "--technology", "react"])
        assert args.technology == "react"

    def test_fetch_subcommand_accepts_interval(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "fetch", "--interval", "3600"])
        assert args.interval == 3600

    def test_fetch_subcommand_interval_defaults_to_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "fetch"])
        assert args.interval is None

    def test_watch_and_fetch_subcommand_recognized(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "watch-and-fetch"])
        assert args.command == "watch-and-fetch"

    def test_watch_and_fetch_default_interval_is_86400(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--base-dir", "/tmp", "watch-and-fetch"])
        assert args.interval == 86400


class TestFetchCommandExecution:
    def test_fetch_all_called_when_no_technology_filter(self, tmp_path: Path) -> None:
        hub = _make_hub(tmp_path)
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_all.return_value = [{"technology": "react", "fetched": True}]

        with patch("buonaiuto_doc4llm.__main__.HttpDocFetcher", return_value=mock_fetcher):
            with patch("buonaiuto_doc4llm.__main__.DocsHubService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.scan.return_value = []
                mock_svc_cls.return_value = mock_svc
                with patch("sys.argv", ["buonaiuto_doc4llm", "--base-dir", str(hub), "fetch"]):
                    from buonaiuto_doc4llm.__main__ import main
                    main()

        mock_fetcher.fetch_all.assert_called_once()

    def test_fetch_technology_called_when_filter_given(self, tmp_path: Path) -> None:
        hub = _make_hub(tmp_path)
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = {"technology": "react", "fetched": True}

        with patch("buonaiuto_doc4llm.__main__.HttpDocFetcher", return_value=mock_fetcher):
            with patch("buonaiuto_doc4llm.__main__.DocsHubService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.scan.return_value = []
                mock_svc_cls.return_value = mock_svc
                with patch("sys.argv", ["buonaiuto_doc4llm", "--base-dir", str(hub), "fetch", "--technology", "react"]):
                    from buonaiuto_doc4llm.__main__ import main
                    main()

        mock_fetcher.fetch.assert_called_once_with("react")

    def test_fetch_scan_called_after_fetch(self, tmp_path: Path) -> None:
        hub = _make_hub(tmp_path)
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_all.return_value = []

        with patch("buonaiuto_doc4llm.__main__.HttpDocFetcher", return_value=mock_fetcher):
            with patch("buonaiuto_doc4llm.__main__.DocsHubService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.scan.return_value = []
                mock_svc_cls.return_value = mock_svc
                with patch("sys.argv", ["buonaiuto_doc4llm", "--base-dir", str(hub), "fetch"]):
                    from buonaiuto_doc4llm.__main__ import main
                    main()

        mock_svc.scan.assert_called_once()

    def test_fetch_interval_loops_and_exits_on_keyboard_interrupt(self, tmp_path: Path) -> None:
        hub = _make_hub(tmp_path)
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_all.return_value = []
        sleep_call_count = 0

        def fake_sleep(n: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            raise KeyboardInterrupt

        with patch("buonaiuto_doc4llm.__main__.HttpDocFetcher", return_value=mock_fetcher):
            with patch("buonaiuto_doc4llm.__main__.DocsHubService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.scan.return_value = []
                mock_svc_cls.return_value = mock_svc
                with patch("time.sleep", side_effect=fake_sleep):
                    with patch("sys.argv", ["buonaiuto_doc4llm", "--base-dir", str(hub), "fetch", "--interval", "1"]):
                        from buonaiuto_doc4llm.__main__ import main
                        main()  # must not raise

        assert sleep_call_count >= 1
