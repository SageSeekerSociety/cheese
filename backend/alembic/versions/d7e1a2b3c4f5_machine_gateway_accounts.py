"""machine_gateway_accounts —— the gateway key a sandbox's model calls go out on

A sandbox asks for a model on its platform socket (`/machine/llm`). Those
calls go out on a key of their own, one per conversation and budget holder,
with a daily cap on the gateway: a machine whose owner relays the socket can
spend that much a day and no more, while the session's own calls keep their
key.

New table only; downgrading drops it, and with it the checkpoints of spend
already charged.

Revision ID: d7e1a2b3c4f5
Revises: c4d5e6f7a8b9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7e1a2b3c4f5"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key takes a lock on `projects`, which the running release
    # writes to.
    with_lock_retries("projects")
    op.create_table(
        "machine_gateway_accounts",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_handle", sa.String(64), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("gateway_key", sa.String(512), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=True),
        sa.Column("daily_budget_usd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint(
            "conversation_id", "owner_handle", name="pk_machine_gateway_accounts"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
            name="fk_machine_gateway_accounts_project_id_projects",
        ),
    )
    op.create_index(
        "ix_machine_gateway_accounts_project_id",
        "machine_gateway_accounts",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_machine_gateway_accounts_project_id",
        table_name="machine_gateway_accounts",
    )
    op.drop_table("machine_gateway_accounts")
