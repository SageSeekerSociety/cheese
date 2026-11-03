"""A space can be open to everyone in an email-suffix scope

`space.open_scope` holds the registered-email suffixes an open space reads to
(`["ruc.edu.cn"]`); NULL is a members-only space, which every existing space
stays. `space_open_request` holds an owner's request to open a space or widen
its scope, which waits for platform review before it reaches `open_scope`.

The column is nullable and the table is new, so the release still serving
while the migration runs reads and writes `space` as before.

Revision ID: 64de28adf082
Revises: f3b9d1c7a5e2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "64de28adf082"
down_revision: str | Sequence[str] | None = "f3b9d1c7a5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("space")
    op.add_column(
        "space",
        sa.Column("open_scope", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "space_open_request",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_space_open_request_status",
        ),
    )
    op.create_index(
        "ix_space_open_request_space_id", "space_open_request", ["space_id"]
    )
    op.create_index(
        "uq_space_open_request_pending",
        "space_open_request",
        ["space_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_table("space_open_request")
    with_lock_retries("space")
    op.drop_column("space", "open_scope")
