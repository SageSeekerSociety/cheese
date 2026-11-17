"""tasks.closed_reason: why the platform closed a task

Revision ID: d7a2e41f8b06
Revises: 4b7ef31b7b66
Create Date: 2026-11-17

A task nobody touched for long enough is closed by the platform
(`task_quiet.neglect`). The reason is kept on the task so the channel's
「最近关闭」 can say so; NULL for every task a person or its teammate closed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7a2e41f8b06"
down_revision: str | Sequence[str] | None = "4b7ef31b7b66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("closed_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "closed_reason")
