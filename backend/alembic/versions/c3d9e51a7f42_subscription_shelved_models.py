"""Subscription shelving becomes 1:N: `llm_subscription_models` replaces
`llm_subscriptions.linked_model_name`.

An imported subscription no longer pushes one hard-wired model onto the gateway;
the operator shelves models from the account's own available list afterwards.
Existing rows keep their linked model as their first shelved row, pointing at
the upstream the deployment default encoded at the time
(``openai/gpt-5.2-codex``).

Revision ID: c3d9e51a7f42
Revises: b672a09ef831
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d9e51a7f42"
down_revision: str | Sequence[str] | None = "b672a09ef831"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_subscription_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("upstream_model", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["llm_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint(
            "subscription_id",
            "upstream_model",
            name="uq_llm_subscription_models_sub_upstream",
        ),
    )
    op.create_index(
        "ix_llm_subscription_models_subscription_id",
        "llm_subscription_models",
        ["subscription_id"],
    )
    # 回填：原来那条写死的 linked 模型变成这条订阅的第一行上架。上游串照当时
    # 的部署默认（settings.subscription_upstream_model 的字面默认），不读配置
    # —— 迁移在任何环境都得给出同一个答案。
    op.execute(
        sa.text(
            """
            INSERT INTO llm_subscription_models
                (id, subscription_id, name, upstream_model, label,
                 created_at, updated_at)
            SELECT gen_random_uuid(), id, linked_model_name,
                   'openai/gpt-5.2-codex', 'GPT · ChatGPT 订阅',
                   created_at, updated_at
            FROM llm_subscriptions
            WHERE linked_model_name IS NOT NULL
            """
        )
    )
    op.drop_column("llm_subscriptions", "linked_model_name")


def downgrade() -> None:
    op.add_column(
        "llm_subscriptions",
        sa.Column("linked_model_name", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE llm_subscriptions s
            SET linked_model_name = m.name
            FROM llm_subscription_models m
            WHERE m.subscription_id = s.id
            """
        )
    )
    op.drop_table("llm_subscription_models")
