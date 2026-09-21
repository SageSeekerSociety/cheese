"""space members, invite codes and visibility

Autogenerate also proposed dropping and recreating `users`, `invite_code`,
`llm_call_log`, `agent_types_archive`, `agent_configuration_migration_backup`
and the gin/fts indexes, and adding a foreign key on `task_access_domain`.
All of that is drift: those objects were created by raw SQL inside older
migrations, so they exist in the database but not in the models autogenerate
compares against. Only the four operations below belong to this change.

Revision ID: 16f4e85e5971
Revises: c8f2a4e91d30
Create Date: 2026-09-19 22:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "16f4e85e5971"
down_revision: str | Sequence[str] | None = "c8f2a4e91d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_member_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_invite_code_seq")))

    # Who is in a space. Separate from space_admin_relation, which answers
    # who may *manage* it: a space's owner and admins stay visible without a
    # row here.
    op.create_table(
        "space_member",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_member_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # One live row per (space, person) — see `SpaceMember.__table_args__` for
    # why it is partial and why the pair still needs its own plain index.
    op.create_index(
        "uq_space_member_active",
        "space_member",
        ["space_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_space_member_space_user", "space_member", ["space_id", "user_id"]
    )

    # Redeeming one of these makes the redeemer a member. Not the platform's
    # `invite_code`, which admits a person to the platform at registration.
    op.create_table(
        "space_invite_code",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_invite_code_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_space_invite_code_code"), "space_invite_code", ["code"], unique=True
    )

    # PUBLIC for every row that already exists, which is exactly what they
    # were: visible to anyone signed in.
    op.add_column(
        "space",
        sa.Column("visibility", sa.SmallInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("space", "visibility")
    op.drop_index(op.f("ix_space_invite_code_code"), table_name="space_invite_code")
    op.drop_table("space_invite_code")
    op.drop_index("ix_space_member_space_user", table_name="space_member")
    op.drop_index("uq_space_member_active", table_name="space_member")
    op.drop_table("space_member")
    op.execute(sa.schema.DropSequence(sa.Sequence("space_invite_code_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_member_seq")))
