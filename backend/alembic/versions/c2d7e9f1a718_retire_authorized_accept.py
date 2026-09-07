"""采纳=当场合并 (#718)：授权语义退场 —— 删授权基线列，存量 pr_open 卡转回 pending

Revision ID: c2d7e9f1a718
Revises: b1e6a4d2c718
Create Date: 2026-09-07 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d7e9f1a718"
down_revision: str | Sequence[str] | None = "b1e6a4d2c718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 授权语义整体消失 (#718)：合并 API 带 head sha（GitHub 409 拦漂移）+ 新提交
    # 作废采纳（dismiss_stale）取代了「冻结授权基线 + 漂移比对」。列随语义一起走。
    op.drop_column("accept_cards", "pr_authorized_sha")
    # 存量在途卡：`pr_open`（采纳=授权、轮询器等绿再合）不再是任何写路径的产物。
    # 已把合并记录在案的极端残留按事实收尾；其余转回 pending —— 授权人的那票留在
    # accept_approvals 里，规则满足时由验收人当场采纳。decided_by/decided_at 是
    # 授权的痕迹，卡回到未决就一并清掉。
    op.execute(
        "UPDATE accept_cards SET status = 'accepted' "
        "WHERE status = 'pr_open' AND pr_merged_at IS NOT NULL"
    )
    op.execute(
        "UPDATE accept_cards SET status = 'pending', decided_by = NULL, "
        "decided_at = NULL, note_code = NULL, note = "
        "'采纳语义已更新（#718）：等 CI 的授权已收回，规则满足时由验收人当场采纳' "
        "WHERE status = 'pr_open'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "accept_cards",
        sa.Column("pr_authorized_sha", sa.String(length=64), nullable=True),
    )
