"""验收卡的评审人列退役（第一步）：`accept_cards.reviewer_handle` 放开非空

上一刀 (`a2f7c1e9d4b3`) 把评审人搬进 `accept_card_reviewer`（一卡一行），列先留着
当镜像，因为旧后端与一批读者还在读它。这一刀把剩下的读者全部迁到表 —— 表成为唯一
说法 —— 并**停止写这一列**。

列本身这一刀不动。迁移规矩（`check-migration-safety.py` 的 `drop-column`）：上一版
release 仍在映射这列时不能删它 —— 删列会让正在服务的旧后端读写当场失败。所以这一刀
只把 `NOT NULL` 摘掉（旧后端照写不误，新后端不写也合法），**删列留给下一刀**。

不丢数据：表在放开非空前已经拥有每一张卡的那一行（上一刀回填，本刀之前的写入又都写
表）。降级先在列里补回缺的那一格，再恢复 `NOT NULL`；一卡多人时**取最早那一行**
（`created_at` 最小的那个）—— 这是这条降级已知的上限，列是单值装不下多人，多出来
的名字不会还原到列里。

Revision ID: f3b8d2c7a1e5
Revises: a2f7c1e9d4b3
Create Date: 2026-10-12
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f3b8d2c7a1e5"
down_revision: str | Sequence[str] | None = "a2f7c1e9d4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("accept_cards")
    # 摘掉 NOT NULL 读不到任何一行（`ALTER COLUMN … DROP NOT NULL` 只改目录），
    # 但它是「新后端可以不写这一列」的前提。列、索引都留着，旧后端照常读写。
    op.alter_column("accept_cards", "reviewer_handle", nullable=True)


def downgrade() -> None:
    with_lock_retries("accept_cards")
    # ponytail: 一卡多人时只抄回最早那一行 —— 列装不下第二个名字，这是降级
    # 的已知上限（升级方向不丢数据）。
    op.execute("""
        UPDATE accept_cards c
        SET reviewer_handle = r.handle
        FROM (
            SELECT DISTINCT ON (card_id) card_id, handle
            FROM accept_card_reviewer
            ORDER BY card_id, created_at
        ) r
        WHERE r.card_id = c.id AND c.reviewer_handle IS NULL
    """)
    op.alter_column("accept_cards", "reviewer_handle", nullable=False)
