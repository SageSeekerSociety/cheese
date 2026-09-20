"""local_fs: 本机目录授权 — grants on a user's own machine, and their audit

Revision ID: d5b2c8f41a37
Revises: f3a8c5d2e917
Create Date: 2026-09-19

Two tables. ``local_directory_grant`` is the authorisation itself: one directory
on one device, read or read-write, scoped to one project or to everything the
owner does, revoked by setting ``revoked_at`` (the row is kept — the audit refers
to it). ``local_fs_access`` is the audit: every decision, allowed or denied.

The anchoring differs on purpose. A grant cascades from ``device``; the audit
does not, because its ``device_id`` and ``grant_id`` are bare with no foreign
key. The audit has to survive the revocation and the disconnection it describes —
which is exactly when somebody asks it a question.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b2c8f41a37"
down_revision: str | Sequence[str] | None = "f3a8c5d2e917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_directory_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column(
            "mode", sa.String(length=16), nullable=False, server_default="read"
        ),
        sa.Column(
            "scope", sa.String(length=16), nullable=False, server_default="project"
        ),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"], ["device.device_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["user.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_local_directory_grant_device_id"),
        "local_directory_grant",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_local_directory_grant_owner_user_id"),
        "local_directory_grant",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_local_directory_grant_key"),
        "local_directory_grant",
        ["key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_local_directory_grant_project_id"),
        "local_directory_grant",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_local_directory_grant_device_live",
        "local_directory_grant",
        ["device_id", "revoked_at"],
        unique=False,
    )

    op.create_table(
        "local_fs_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("actor_handle", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_local_fs_access_device_id"),
        "local_fs_access",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_local_fs_access_created_at"),
        "local_fs_access",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_local_fs_access_device_time",
        "local_fs_access",
        ["device_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_local_fs_access_device_time", table_name="local_fs_access")
    op.drop_index(op.f("ix_local_fs_access_created_at"), table_name="local_fs_access")
    op.drop_index(op.f("ix_local_fs_access_device_id"), table_name="local_fs_access")
    op.drop_table("local_fs_access")

    op.drop_index(
        "ix_local_directory_grant_device_live", table_name="local_directory_grant"
    )
    op.drop_index(
        op.f("ix_local_directory_grant_project_id"),
        table_name="local_directory_grant",
    )
    op.drop_index(
        op.f("ix_local_directory_grant_key"), table_name="local_directory_grant"
    )
    op.drop_index(
        op.f("ix_local_directory_grant_owner_user_id"),
        table_name="local_directory_grant",
    )
    op.drop_index(
        op.f("ix_local_directory_grant_device_id"),
        table_name="local_directory_grant",
    )
    op.drop_table("local_directory_grant")
