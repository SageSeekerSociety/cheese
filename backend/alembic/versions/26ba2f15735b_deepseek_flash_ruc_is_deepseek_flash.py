"""projects and agents on deepseek-flash-ruc move to deepseek-flash

Revision ID: 26ba2f15735b
Revises: e8d439b81ab1
Create Date: 2026-10-10

The gateway no longer offers RUC's copy of DeepSeek V4.1 Flash under a name of
its own: it is the first deployment of deepseek-flash, tried before DeepSeek's
API. A selection still naming deepseek-flash-ruc would be refused at its next
turn, so it names the model it was all along.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "26ba2f15735b"
down_revision: str | Sequence[str] | None = "e8d439b81ab1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projects
        SET settings = jsonb_set(settings::jsonb, '{default_model}', '"deepseek-flash"')::json
        WHERE settings::jsonb ->> 'default_model' = 'deepseek-flash-ruc'
        """
    )
    op.execute(
        """
        UPDATE agent_instances
        SET configuration = jsonb_set(configuration::jsonb, '{model}', '"deepseek-flash"')::json
        WHERE configuration::jsonb ->> 'model' = 'deepseek-flash-ruc'
        """
    )


def downgrade() -> None:
    """Leaves every selection on deepseek-flash: which ones named the RUC copy
    is not kept, and the gateway no longer routes that name."""
