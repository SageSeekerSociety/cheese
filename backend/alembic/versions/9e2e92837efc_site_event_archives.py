"""Record where each batch of retired 现场 rows went

Revision ID: 9e2e92837efc
Revises: b7d2e94a1c05
Create Date: 2026-10-20

`app.domain.session_record.retired_site` moves the hidden event rows the old
现场 wrote out of `blocks` into the private bucket, a batch at a time. Each
batch's row here names the object that holds it and is written in the
transaction that deletes those rows. A new table, so nothing serving reads it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e2e92837efc"
down_revision: str | Sequence[str] | None = "b7d2e94a1c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_event_archives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_event_archives"),
    )


def downgrade() -> None:
    op.drop_table("site_event_archives")
