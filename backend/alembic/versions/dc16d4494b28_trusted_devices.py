"""Trust a browser to skip two-step verification for 30 days

One row per trusted browser, holding a digest of the token in its cookie, and
a flag on each sign-in that a trusted browser stood in for the second step.

Revision ID: dc16d4494b28
Revises: 6810970b13b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "dc16d4494b28"
down_revision: str | Sequence[str] | None = "6810970b13b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "two_factor_skipped",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_table(
        "user_trusted_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("user_agent", sa.String(length=1024), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["user_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_user_trusted_devices_user_id"),
        "user_trusted_devices",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_trusted_devices_session_id"),
        "user_trusted_devices",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_trusted_devices_session_id"), table_name="user_trusted_devices"
    )
    op.drop_index(
        op.f("ix_user_trusted_devices_user_id"), table_name="user_trusted_devices"
    )
    op.drop_table("user_trusted_devices")
    op.drop_column("user_sessions", "two_factor_skipped")
