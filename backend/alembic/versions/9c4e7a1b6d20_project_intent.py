"""project intent — 用户在建项目时写下的「我打算做什么」

#946 片 C：建项目时问一句，有值就存下来并搬进新生房间的简报。与 `summary`
（AI 维护的一页纸）分开两列，因为「谁说的」不同，合并会让两者互相覆盖。

Revision ID: 9c4e7a1b6d20
Revises: b7c4e19f2a83
Create Date: 2026-09-20 09:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c4e7a1b6d20"
down_revision: str | Sequence[str] | None = "b7c4e19f2a83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects", sa.Column("intent", sa.Text(), server_default="", nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "intent")
