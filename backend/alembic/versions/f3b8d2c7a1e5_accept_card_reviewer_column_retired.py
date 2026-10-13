"""验收卡的评审人列退役：删 `accept_cards.reviewer_handle`

上一刀 (`a2f7c1e9d4b3`) 把评审人搬进 `accept_card_reviewer`（一卡一行），列先留着
当镜像，因为旧后端与一批读者还在读它。这一刀把剩下的读者全部迁到表，然后删列 ——
`accept_card_reviewer` 成为唯一说法。

不丢数据：表在删列之前已经拥有每一张卡的那一行（上一刀回填，本刀之前的写入又都写
表）。降级把列按旧语义重建，并从表里抄回一个评审人：一卡多人时**取最早那一行**
（`created_at` 最小的那个）—— 这是这条降级已知的上限，列是单值装不下多人，多出来
的名字不会还原到列里。

Revision ID: f3b8d2c7a1e5
Revises: a2f7c1e9d4b3
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f3b8d2c7a1e5"
down_revision: str | Sequence[str] | None = "a2f7c1e9d4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_accept_cards_reviewer_handle"


def upgrade() -> None:
    with_lock_retries("accept_cards")
    op.drop_index(_INDEX, table_name="accept_cards")
    op.drop_column("accept_cards", "reviewer_handle")


def downgrade() -> None:
    with_lock_retries("accept_cards")
    # 先带一个临时默认值加列（表里已有行，非空列必须给默认），回填后再摘掉，
    # 恢复旧列「nullable=False 且无 server_default」的形状。
    op.add_column(
        "accept_cards",
        sa.Column(
            "reviewer_handle",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.create_index(_INDEX, "accept_cards", ["reviewer_handle"])
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
        WHERE r.card_id = c.id
    """)
    op.alter_column("accept_cards", "reviewer_handle", server_default=None)
