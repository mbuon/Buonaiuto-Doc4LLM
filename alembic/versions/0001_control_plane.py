"""Initial control plane schema with workspace isolation policies.

Revision ID: 0001_control_plane
Revises:
Create Date: 2026-03-18 14:50:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )

    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE workspace_memberships ENABLE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY workspace_isolation_workspaces ON workspaces
        USING (id = current_setting('app.workspace_id', true));
        """
    )
    op.execute(
        """
        CREATE POLICY workspace_isolation_workspace_memberships ON workspace_memberships
        USING (workspace_id = current_setting('app.workspace_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS workspace_isolation_workspace_memberships ON workspace_memberships;")
    op.execute("DROP POLICY IF EXISTS workspace_isolation_workspaces ON workspaces;")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")

