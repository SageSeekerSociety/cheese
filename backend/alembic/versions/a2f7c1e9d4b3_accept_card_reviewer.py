"""验收卡的评审人从单值列搬进一张共用表

`accept_cards.reviewer_handle` 这一列记着「这张卡递给谁」。它把「一卡一人」这
条不变量藏在**列的类型里**（一个字符串就是一个评审人），所以哪天要变成多人评分，
得先动列；这一刀把评审人搬进 `accept_card_reviewer`（一卡一行，今天恰好一行），
不变量改由一处显式校验维持（`AcceptCardRepository.set_reviewer`）。

不改行为：回填把每张现存卡的 `reviewer_handle` 抄成一行，列**先留着**（旧后端与
大量读者仍在读它，本迁移只增表、不删列、不改列）。降级丢掉这张新表即可 —— 数据
的权威副本那一段始终还在列里，不丢。

Revision ID: a2f7c1e9d4b3
Revises: e4b1c7a90f23
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a2f7c1e9d4b3"
down_revision: str | Sequence[str] | None = "e4b1c7a90f23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新表的外键指向 accept_cards，建约束会锁它 —— 按迁移规矩先把它锁住。
    with_lock_retries("accept_cards")
    op.create_table(
        "accept_card_reviewer",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["accept_cards.id"],
            name="fk_accept_card_reviewer_card_id_accept_cards",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "card_id", "handle", name="uq_accept_card_reviewer_card_id_handle"
        ),
    )
    op.create_index(
        "ix_accept_card_reviewer_card_id", "accept_card_reviewer", ["card_id"]
    )
    # 回填：每张现存的卡恰好一个评审人 —— 正是这一刀要保住的那个不变量。
    op.execute("""
        INSERT INTO accept_card_reviewer (id, card_id, handle, created_at, updated_at)
        SELECT gen_random_uuid(), id, reviewer_handle, now(), now()
        FROM accept_cards
    """)


def downgrade() -> None:
    op.drop_index("ix_accept_card_reviewer_card_id", table_name="accept_card_reviewer")
    op.drop_table("accept_card_reviewer")
