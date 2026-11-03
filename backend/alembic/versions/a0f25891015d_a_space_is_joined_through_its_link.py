"""a space is joined through its link, not an invite code

Every space gets a join link, the shape a team's already has: ``join_token``
(permanent until an owner or admin resets it; NULL until first asked for, then
minted) and ``join_approval`` (whether joining waits for an owner or admin; off
by default, every existing space included). ``space_join_request`` holds the
requests the link produces while approval is on.

Invite codes stop working with this release: the code that redeemed them is
gone, so ``space_invite_code`` and ``space_member.invite_code_id`` are no longer
read or written. Existing members stay. The table and the column are dropped by
a later migration, once no release still serving maps them (two releases, as for
any dropped column); until then they are listed in the schema-drift baseline.

The new columns are nullable or carry a server default, and the new table is
unknown to the previous release, so it keeps serving while this runs.

Revision ID: a0f25891015d
Revises: f3b9d1c7a5e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a0f25891015d"
down_revision: str | Sequence[str] | None = "f3b9d1c7a5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKEN_INDEX = "uq_space_join_token"


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    with_lock_retries("space")
    op.add_column("space", sa.Column("join_token", sa.String(64), nullable=True))
    op.add_column(
        "space",
        sa.Column(
            "join_approval", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "space_join_request",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "space_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "space.id",
                ondelete="CASCADE",
                name="fk_space_join_request_space_id_space",
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_space_join_request_status",
        ),
    )
    op.create_index(
        "ix_space_join_request_space_id", "space_join_request", ["space_id"]
    )
    op.create_index(
        "uq_space_join_request_pending",
        "space_join_request",
        ["space_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    # The link is looked up by its token, and two spaces never share one.
    # CONCURRENTLY because the serving backend still writes ``space``.
    with op.get_context().autocommit_block():
        if _invalid(TOKEN_INDEX):
            op.drop_index(TOKEN_INDEX, table_name="space", postgresql_concurrently=True)
        op.create_index(
            TOKEN_INDEX,
            "space",
            ["join_token"],
            unique=True,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            TOKEN_INDEX,
            table_name="space",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_table("space_join_request")
    with_lock_retries("space")
    op.drop_column("space", "join_approval")
    op.drop_column("space", "join_token")
