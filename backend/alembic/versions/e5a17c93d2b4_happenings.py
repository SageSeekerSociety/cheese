"""What happened, said where it happens, for the rules waiting for it

A person's routines that wait for something used to read the person's
notifications on a clock, a little behind, from where each had read up to
(`assistant_routines.event_cursor`). What happens is now said in the
transaction it happens in, one row in `happenings` each, and read once by the
sweep it wakes. Nothing reads the cursor any more; the column is dropped in a
later release, once no deployed backend maps it.

A run says what started it as its conversation shows it
(`assistant_runs.shown`): a rule waiting once runs in the conversation it
was asked for in, and the line there names what happened.

Revision ID: e5a17c93d2b4
Revises: a7d3e5c91f20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5a17c93d2b4"
down_revision: str | Sequence[str] | None = "a7d3e5c91f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries('"user", assistant_runs')
    op.create_table(
        "happenings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(
                "user.id", ondelete="CASCADE", name="fk_happenings_user_id_user"
            ),
            nullable=True,
        ),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_happenings_user_id", "happenings", ["user_id"])
    op.add_column(
        "assistant_runs", sa.Column("shown", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assistant_runs", "shown")
    op.drop_table("happenings")
