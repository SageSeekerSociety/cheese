"""a delivery is a row before it is a send

Revision ID: e3a7c15d80b4
Revises: d7b3f0a9c651
Create Date: 2026-09-20 23:10:00

投递账本（结论 58）。没送到的投递以前没有任何一行记着它本该发出去 —— 去重住在一个
带 TTL 的 Redis 键里，重启即失效，所以「已经算过要发给他」这件事在崩溃之后无从查
起。`deliveries` 是那份记录：谁该收到、凭哪个去重键、写下来的时刻、发出去的时刻。

`notification.delivery_key` 是同一个键落在收件箱那一行上。它必须在收件箱这一侧，因
为「站内信已经收下、这一批却没算送到」那一档（排在站内信后面的渠道抛了，整批
`dispatch()` 返回 False）在账本里看起来和「根本没发」一模一样：补发再发一遍，插入撞
上这个唯一约束，收件人手里仍然只有一条。

NULL 允许且互不排斥（Postgres 的唯一索引不认为两个 NULL 相等）：还没走账本的那些调
用点照旧每次都插入，这一列对它们是空的。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3a7c15d80b4"
down_revision: str | Sequence[str] | None = "d7b3f0a9c651"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_handle", sa.String(length=64), nullable=False),
        sa.Column("receiver_id", sa.BigInteger(), nullable=False),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key"),
    )
    # 补发查的是 `sent_at IS NULL AND attempts < N ORDER BY recorded_at`，全表只有
    # 这一处读。部分索引只收还没送出去的那些：这张表每发一条通知插一行，把已经送到
    # 的也索引进去就是把写放大挂在通知的热路径上。`event_id` 不建索引 —— 没有一处
    # 按它查，那一列留着做事后取证。
    op.create_index(
        "idx_deliveries_unsent",
        "deliveries",
        ["recorded_at"],
        postgresql_where=sa.text("sent_at IS NULL"),
    )
    op.add_column(
        "notification",
        sa.Column("delivery_key", sa.String(length=160), nullable=True),
    )
    op.create_unique_constraint(
        "uq_notification_delivery_key", "notification", ["delivery_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_notification_delivery_key", "notification", type_="unique")
    op.drop_column("notification", "delivery_key")
    op.drop_index("idx_deliveries_unsent", table_name="deliveries")
    op.drop_table("deliveries")
