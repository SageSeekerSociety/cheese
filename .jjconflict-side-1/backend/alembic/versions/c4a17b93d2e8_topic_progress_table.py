"""topic_progress table — 进度层 survives the machine (#187)

芝士's checklist (the Task tools' working log) only ever existed in the turn's
WS stream, so it died with the turn and with the machine. That is the direct
cause of "换了机器不知道自己做到哪": code, decisions and the doc all survive a
body swap, but the half-finished work between them did not.

One row per topic, overwritten in place — current state, not history (the
conversation timeline already keeps history). Deliberately NOT stored as
memory: memory is stable facts injected into every prompt, and a running
checklist would both bloat it and go stale.

Revision ID: c4a17b93d2e8
Revises: b8e1d4c70a92
Create Date: 2026-08-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a17b93d2e8"
down_revision: str | Sequence[str] | None = "b8e1d4c70a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "topic_progress",
        # The topic id IS the primary key: exactly one checklist per topic, and
        # the upsert path wants it as the conflict target.
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("topic_progress")
