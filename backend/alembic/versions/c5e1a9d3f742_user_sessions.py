"""Keep sign-in sessions in Postgres (#1481)

One row per sign-in: the refresh-token family behind it, and the entry the
security page lists. Refresh, sign-out and revocation all read it, which the
Redis session records they replace never were. Nothing is copied from Redis:
those records named no refresh token, so every signed-in user signs in once
more after this revision.

Revision ID: c5e1a9d3f742
Revises: e2ccbf34a7bd
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e1a9d3f742"
down_revision: str | Sequence[str] | None = "e2ccbf34a7bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("login_method", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=1024), nullable=False),
        sa.Column("ip", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_hash"),
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_sessions_previous_hash"),
        "user_sessions",
        ["previous_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_sessions_previous_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
