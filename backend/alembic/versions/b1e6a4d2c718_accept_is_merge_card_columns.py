"""采纳=当场合并 (#718)：卡的合并态镜像 + auto-merge 布防列

Revision ID: b1e6a4d2c718
Revises: c8f21d4a7e93
Create Date: 2026-09-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1e6a4d2c718"
down_revision: str | Sequence[str] | None = "c8f21d4a7e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 卡上的状态直接用合并态 (#718)：轮询器每拍把算出的 verdict 镜像到这里
    # （state / reasons / who / head / 时间），describe() 只读镜像、不打 GitHub。
    op.add_column("accept_cards", sa.Column("merge_state", sa.JSON(), nullable=True))
    # 绿了自动合（GitHub auto-merge 的对应物）：谁、什么时候布的防。布防不是
    # 决议 —— 卡留在 pending，规则满足时轮询器以布防人的名义合并；新提交作废
    # 采纳（dismiss_stale）同样解除布防。
    op.add_column(
        "accept_cards",
        sa.Column("auto_merge_armed_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accept_cards",
        sa.Column("auto_merge_armed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("accept_cards", "auto_merge_armed_at")
    op.drop_column("accept_cards", "auto_merge_armed_by")
    op.drop_column("accept_cards", "merge_state")
