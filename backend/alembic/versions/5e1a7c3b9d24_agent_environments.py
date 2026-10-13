"""Each agent's execution environment, and the defaults rooms and tasks hold

Revision ID: 5e1a7c3b9d24
Revises: 76d8e010047a
Create Date: 2026-10-12

`agent_environments` holds the execution environment one agent works in, in
one conversation: a sandbox of some spec, or one self-hosted machine
(`agent.environment`). `topics.default_environment` and
`tasks.default_environment` hold what a new agent there gets. The columns they
replace (`topics.compute_config`, `tasks.compute_config`) stay until the
release after this one stops reading them; the next migration copies their
values across.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5e1a7c3b9d24"
down_revision: str | Sequence[str] | None = "76d8e010047a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("topics, tasks, conversations")
    op.create_table(
        "agent_environments",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(64), nullable=False),
        sa.Column("environment", postgresql.JSONB(), nullable=False),
        sa.Column("chosen_by", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_agent_environments_conversation_id",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "agent_handle"),
    )
    op.add_column(
        "topics", sa.Column("default_environment", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("default_environment", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "default_environment")
    op.drop_column("topics", "default_environment")
    op.drop_table("agent_environments")
