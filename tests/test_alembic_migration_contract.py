from pathlib import Path


def test_initial_control_plane_migration_enables_rls() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    migration = repo_root / "alembic/versions/0001_control_plane.py"
    content = migration.read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in content
    assert "CREATE POLICY workspace_isolation_workspaces" in content
    assert "CREATE POLICY workspace_isolation_workspace_memberships" in content
