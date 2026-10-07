"""A routine run is a message of its teammate's in the channel's main line

Revision ID: 76632669f1f0
Revises: b7e2d41c9a06
Create Date: 2026-10-07

``routine_runs.message_id``: the run's message, whose 支线 the run happens in
and which says how it went. Runs from before have none: they were told as
platform lines, which stay where they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "76632669f1f0"
down_revision: str | Sequence[str] | None = "b7e2d41c9a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "routine_runs",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey(
                "blocks.id",
                ondelete="SET NULL",
                name="fk_routine_runs_message_id",
            ),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_routine_runs_message_id", "routine_runs", ["message_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_routine_runs_message_id", "routine_runs", type_="unique")
    op.drop_column("routine_runs", "message_id")
