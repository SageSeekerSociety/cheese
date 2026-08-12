"""agent tokens: a member's own agent gets its own credential and identity

Adds ``agent_tokens`` (the credential; only a SHA-256 of the secret is stored)
and ``agent_bindings.owner_user_id`` (whose agent this is — NULL keeps every
existing binding the platform's own 芝士, which is what they all are).

Revision ID: c1d5f8a30b47
Revises: b8e1d4c70a92
Create Date: 2026-08-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d5f8a30b47"
down_revision: str | Sequence[str] | None = "b8e1d4c70a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "agent_bindings",
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_bindings_owner_user_id"),
        "agent_bindings",
        ["owner_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_agent_bindings_owner_user_id_user",
        "agent_bindings",
        "user",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("agent_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_tokens_owner_user_id"),
        "agent_tokens",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_tokens_agent_user_id"),
        "agent_tokens",
        ["agent_user_id"],
        unique=False,
    )
    # Unique because authentication looks a presented secret up BY its digest —
    # the index is the lookup path, not just an integrity constraint.
    op.create_index(
        op.f("ix_agent_tokens_token_hash"),
        "agent_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_agent_tokens_token_hash"), table_name="agent_tokens")
    op.drop_index(op.f("ix_agent_tokens_agent_user_id"), table_name="agent_tokens")
    op.drop_index(op.f("ix_agent_tokens_owner_user_id"), table_name="agent_tokens")
    op.drop_table("agent_tokens")
    op.drop_constraint(
        "fk_agent_bindings_owner_user_id_user", "agent_bindings", type_="foreignkey"
    )
    op.drop_index(op.f("ix_agent_bindings_owner_user_id"), table_name="agent_bindings")
    op.drop_column("agent_bindings", "owner_user_id")
