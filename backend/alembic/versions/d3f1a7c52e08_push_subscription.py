"""push_subscription: one row per browser that agreed to receive push

#1084 第 5 步。浏览器推送需要三样东西存下来 —— 投递地址（`endpoint`）和两把加密
材料（`p256dh` / `auth`）—— 因为 Web Push 的内容由**浏览器的**密钥加密，服务端既
看不到明文也解不开。

`endpoint` 唯一：同一个浏览器重复订阅换回同一个值，按它去重，订阅不会越攒越多。
`user_id` 带 ON DELETE CASCADE：一个投不到任何人身上的投递地址没有意义。

两个索引各自有读者：`user_id` 是「发给这个人的全部浏览器」（每条推送都要问），
`endpoint` 的唯一约束同时服务订阅时的 upsert。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f1a7c52e08"
down_revision: str | Sequence[str] | None = "c4d81f6a27b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_subscription",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
    )
    op.create_index(
        "ix_push_subscription_user_id", "push_subscription", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_push_subscription_user_id", table_name="push_subscription")
    op.drop_table("push_subscription")
