"""when an account was given its first project

Revision ID: 8083d813e832
Revises: d45f136655ef
Create Date: 2026-10-10 10:00:00.000000

一列，可空：`POST /users/me/first-project` 给这个账号建第一个项目时写。已有的账号
不回填：有项目的人不会走到那条路；一个项目都没有的老账号下次进来会得到一个，这正是
那条路要做的事。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8083d813e832"
down_revision: str | Sequence[str] | None = "d45f136655ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("first_project_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "first_project_at")
