"""Bind projects to their authoritative forge and persist credential leases.

Revision ID: a1286f09c001
Revises: c8a1d5e73f20
"""

import sqlalchemy as sa

from alembic import op

revision = "a1286f09c001"
down_revision = "c8a1d5e73f20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("author_handle", sa.String(64), nullable=True))
    op.create_table(
        "project_forges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("api_url", sa.String(2048), nullable=False),
        sa.Column("repo", sa.String(255), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=False),
        sa.Column("account_password", sa.Text(), nullable=True),
    )
    op.execute("""
        INSERT INTO project_forges
            (id, project_id, kind, url, api_url, repo, default_branch)
        SELECT gen_random_uuid(), project_id, 'github_app',
            'https://github.com/' || repo || '.git', 'https://api.github.com',
            repo, ''
        FROM project_git_installations
    """)
    op.create_table(
        "forge_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("api_url", sa.String(2048), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_forge_tokens_project_id", "forge_tokens", ["project_id"])
    op.create_index("ix_forge_tokens_expires_at", "forge_tokens", ["expires_at"])
    op.create_table(
        "task_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("snapshot_sha", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
    )
    op.create_index("ix_task_snapshots_task_id", "task_snapshots", ["task_id"])


def downgrade():
    op.drop_column("tasks", "author_handle")
    op.drop_table("task_snapshots")
    op.drop_table("forge_tokens")
    op.drop_table("project_forges")
