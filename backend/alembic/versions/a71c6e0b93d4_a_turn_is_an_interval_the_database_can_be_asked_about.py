"""a turn is an interval the database can be asked about

The in-flight turn registry was a JSON file at
``{workspace_root}/.turns-inflight.json``. It did the one job it was written for
— survive the process — and paid for it everywhere else: host-local, so it could
not be read next to the topic it describes nor joined against the blocks that
carry the same ``turn_id``; and a registry of the LIVING, since an entry existed
only while a turn ran and was deleted at the end. That last part is why the
orphan sweep had to reconstruct "did this prompt reach the session" from
forensics — does any block bear this turn id, is there anything in the spool —
instead of reading the answer the runtime already knew and wrote down.

``agent_turns`` is that registry as an interval: ``started_at``,
``delivered_at``, ``stopped_at``, running is ``stopped_at IS NULL``. Rows are
closed, never deleted.

Nothing is backfilled. The file holds only turns in flight at this instant, and
importing them would be a compatibility path that never leaves for one deploy's
worth of turns — whose output the spool settle lands anyway. The cost is that
turns interrupted by the deploy that ships this get no automatic re-send.

Revision ID: a71c6e0b93d4
Revises: b3e91d47c05a
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a71c6e0b93d4"
down_revision: str | None = "b3e91d47c05a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("continuation_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_resume", sa.Boolean(), nullable=False),
        sa.Column("resendable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_turns_topic_id", "agent_turns", ["topic_id"])
    # The sweep's only question is which intervals are still open, and that set
    # is a handful of rows next to every turn the platform has ever run.
    op.create_index(
        "ix_agent_turns_open",
        "agent_turns",
        ["started_at"],
        postgresql_where=sa.text("stopped_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_turns_open", table_name="agent_turns")
    op.drop_index("ix_agent_turns_topic_id", table_name="agent_turns")
    op.drop_table("agent_turns")
