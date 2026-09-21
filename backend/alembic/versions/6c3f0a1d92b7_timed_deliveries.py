"""the timed-delivery primitive: one row per alarm a participant set

结论 17 的那条原语落库的地方：任何参与者可以请求「某时刻把这条投递给我」，这张表存
的就是那个请求 —— 收件人、要递的那句话、该在什么时候递、递过没有。

它和 `deliveries` 分开，因为两张表记的不是同一件事：账本那一行是一条**已经发生**的
事件送到没送到，这一行是一条**还没有发生**的事件和它该发生的时刻。塞进账本就等于让
补发那条扫描去面对一批故意还没送的行，而那条扫描的全部含义是「没送到的再送一次」。

Revision ID: 6c3f0a1d92b7
Revises: c9f41b7a2e08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6c3f0a1d92b7"
down_revision: str | Sequence[str] | None = "c9f41b7a2e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timed_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_handle", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 每一拍扫的就是这一条。部分索引：递过的行只会越积越多，而没有一处按它们查。
    op.create_index(
        "idx_timed_deliveries_due",
        "timed_deliveries",
        ["due_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_timed_deliveries_due", table_name="timed_deliveries")
    op.drop_table("timed_deliveries")
