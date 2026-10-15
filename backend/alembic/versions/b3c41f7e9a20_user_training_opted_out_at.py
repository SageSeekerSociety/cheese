"""when a person opted out of their content being used for training

Revision ID: b3c41f7e9a20
Revises: b29905fee2b7
Create Date: 2026-10-11 04:00:00.000000

一列，可空：这个人拒绝把自己的内容用于模型训练、提供给合作方的时间（隐私政策
1.2）。已有的账号不回填：没拒绝过就是 NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c41f7e9a20"
down_revision: str | Sequence[str] | None = "b29905fee2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("training_opted_out_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "training_opted_out_at")
