"""accept_cards.pr_merged_by_id / pr_merged_by_login: who merged the PR

Revision ID: c7a4e91d2b68
Revises: b5d1e9a3c7f2
Create Date: 2026-10-21 16:48:00.000000

两列，可空：卡在平台里被采纳时由 `decided_by` 说话；PR 直接在 GitHub 上合掉
的那种卡没有点击人，这两列记 GitHub 报的合并人 —— 数字 id 用来对平台账号，
登录名在账号没连过知是时拿来显示。

`pr_merged_by_login` 为 NULL 是「还没问过」，空串是「问过、没人可写」。存量由
轮询器的补录一拍几张补上（`review/services/merge_actor_backfill.py`），迁移里
不回填，也不需要回填。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a4e91d2b68"
down_revision: str | Sequence[str] | None = "d41f7b2c9e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accept_cards",
        sa.Column("pr_merged_by_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accept_cards",
        sa.Column("pr_merged_by_login", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "pr_merged_by_login")
    op.drop_column("accept_cards", "pr_merged_by_id")
