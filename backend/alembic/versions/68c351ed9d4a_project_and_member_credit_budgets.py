"""Project budgets, member task caps and execution-time credit attribution.

Revision ID: 68c351ed9d4a
Revises: 569e06806d03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "68c351ed9d4a"
down_revision: str | Sequence[str] | None = "569e06806d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    with_lock_retries("projects, conversations, resource_usage")
    op.create_table(
        "project_credit_budgets",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("credits_limit", sa.Float(), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("project_id", name="pk_project_credit_budgets"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_credit_budgets_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "credits_limit IS NULL OR (credits_limit >= 0 AND credits_limit < 'Infinity'::float8)",
            name="ck_project_credit_budgets_limit",
        ),
    )
    op.create_table(
        "member_credit_budgets",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("credits_limit", sa.Float(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint(
            "project_id", "owner_handle", name="pk_member_credit_budgets"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_member_credit_budgets_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "credits_limit >= 0 AND credits_limit < 'Infinity'::float8",
            name="ck_member_credit_budgets_limit",
        ),
    )
    op.create_table(
        "work_credit_owners",
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("work_id", name="pk_work_credit_owners"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_work_credit_owners_project_id_projects",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_work_credit_owners_conversation_id",
        "work_credit_owners",
        ["conversation_id"],
    )
    op.create_index(
        "ix_work_credit_owners_project_id", "work_credit_owners", ["project_id"]
    )
    op.create_table(
        "task_gateway_accounts",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gateway_key", sa.String(512), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=True),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint(
            "conversation_id", "owner_handle", name="pk_task_gateway_accounts"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_task_gateway_accounts_project_id_projects",
        ),
    )
    op.create_index(
        "ix_task_gateway_accounts_project_id", "task_gateway_accounts", ["project_id"]
    )
    op.add_column(
        "resource_usage", sa.Column("credit_owner_handle", sa.String(64), nullable=True)
    )
    # Deleting a conversation cannot refund costs already paid by its project.
    op.drop_constraint(
        "fk_resource_usage_conversation_id", "resource_usage", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_resource_usage_conversation_id_conversations",
        "resource_usage",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    # Historical usage has no reliable person attribution and remains unknown.


def downgrade() -> None:
    raise NotImplementedError("Credit attribution is retained as accounting history")
