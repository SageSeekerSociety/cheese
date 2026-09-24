"""llm_subscriptions 增加 upstream_model：一条订阅喂给网关的上游模型。

此前上游模型只有一个全局答案（``settings.subscription_upstream_model``），
导入时选定其他模型（订阅账号里实际可用的 GPT-5.6 Luna/Sol 等）没有入口。
这一列存的是**显式选择**：NULL = 跟随全局默认（读取时在
``services._push_to_gateway`` 回落到 settings，热配行为保留），不回填——
回填会把存量行冻结在导入那一刻的默认值上。

Revision ID: b3a91c7e52f0
Revises: 7c2e5d1a9b40
"""

import sqlalchemy as sa

from alembic import op

revision = "b3a91c7e52f0"
down_revision = "7c2e5d1a9b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No backfill: NULL means "follow the deployment default", resolved at
    # push time against settings.subscription_upstream_model.
    op.add_column(
        "llm_subscriptions",
        sa.Column("upstream_model", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_subscriptions", "upstream_model")
