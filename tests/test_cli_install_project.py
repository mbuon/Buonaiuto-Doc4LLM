from buonaiuto_doc4llm.__main__ import build_parser


def test_cli_parser_accepts_install_project_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--base-dir",
            "/tmp/base",
            "install-project",
            "/tmp/myproject",
            "--project-id",
            "myproject",
        ]
    )

    assert args.command == "install-project"
    assert args.project_path == "/tmp/myproject"
    assert args.project_id == "myproject"


def test_cli_parser_accepts_serve_with_project_bootstrap_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--base-dir",
            "/tmp/base",
            "serve",
            "--project-path",
            "/tmp/myproject",
            "--project-id",
            "myproject",
        ]
    )

    assert args.command == "serve"
    assert args.project_path == "/tmp/myproject"
    assert args.project_id == "myproject"
