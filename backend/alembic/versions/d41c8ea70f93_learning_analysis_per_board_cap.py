"""学情分析：每块板自己的一天调用上限（空着跟平台默认）

Revision ID: d41c8ea70f93
Revises: b4e7a2c91d35
Create Date: 2026-11-22

实时学情分析（#5388）之后跟着补的一格：平台的默认上限从 200 提到 2000，这块板可以带
自己的数。可空、不回填（规则 3）：NULL 表示「跟着平台默认走」，把 2000 抄进每一行会让
以后调默认值时老板停在 2000。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d41c8ea70f93"
down_revision: str | Sequence[str] | None = "b4e7a2c91d35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("space")
    op.add_column(
        "space",
        sa.Column("learning_analysis_daily_calls", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("space", "learning_analysis_daily_calls")
