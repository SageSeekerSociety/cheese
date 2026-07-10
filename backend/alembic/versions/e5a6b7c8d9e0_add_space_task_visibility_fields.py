"""Add space task visibility fields.

Revision ID: e5a6b7c8d9e0
Revises: 8c9d0e1f2a3b
Create Date: 2026-05-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "8c9d0e1f2a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("space", sa.Column("visible_task_limit", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE task SET published_at = updated_at WHERE approved = 0 AND published_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("space", "visible_task_limit")
    op.drop_column("task", "ended_at")
    op.drop_column("task", "published_at")
