"""a person's time zone, so quiet hours run on their own clock

Revision ID: e8e05b3cfe1f
Revises: b1a7f3c9e204
Create Date: 2026-10-06 10:00:00.000000

一列，可空：页面打开时把浏览器的时区报上来才有值。没有值的人按北京时间算
（`preferences.DEFAULT_TIMEZONE`），所以不需要回填。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8e05b3cfe1f"
down_revision: str | Sequence[str] | None = "b1a7f3c9e204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "timezone")
