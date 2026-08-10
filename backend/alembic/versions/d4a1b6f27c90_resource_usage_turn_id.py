"""resource_usage.turn_id — group a turn's rows so 轮次 counts turns, not rows

Revision ID: d4a1b6f27c90
Revises: b91c4d7e2a05
Create Date: 2026-08-10 18:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a1b6f27c90"
# Rechained off c4e8f19b0d73 onto b91c4d7e2a05: main landed that one on the same
# parent while this branch was open, and two children of one revision are two
# heads (single-head discipline, .claude/rules/migrations.md).
down_revision: str | Sequence[str] | None = "b91c4d7e2a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # One turn writes several usage rows (a metering-proxy line per
    # /v1/messages, plus the gateway's deferred backfill), so the 资源 panel's
    # row count reported 43 turns for a 3-turn topic. Nullable: rows written
    # before this column — and proxy lines that fall outside any known turn —
    # have no turn to name, and the aggregate counts those one each rather than
    # inventing an attribution. No FK: turn ids are chat-loop identifiers, not
    # rows in a turns table.
    op.add_column(
        "resource_usage",
        sa.Column("turn_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_resource_usage_turn_id"), "resource_usage", ["turn_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_resource_usage_turn_id"), table_name="resource_usage")
    op.drop_column("resource_usage", "turn_id")
