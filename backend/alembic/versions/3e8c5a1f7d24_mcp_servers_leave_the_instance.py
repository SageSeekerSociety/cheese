"""mcp_servers leaves a saved configuration

Revision ID: 3e8c5a1f7d24
Revises: b7c2e4f1a903
Create Date: 2026-09-28 12:00:00

An agent's MCP servers belong to its type (`AgentTypeDef.mcp_servers`) and a
session reads them from there. The copy creation used to write into
``agent_instances.configuration`` was never read by any harness, and an
instance must not override its type, so the key is cleared out of every row.

No release ever read the values, so clearing them changes nothing a session
did. The image being replaced during rollout builds a configuration from a row
without the key (it defaults to an empty list), so no release has to go
between the code change and this.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3e8c5a1f7d24"
down_revision: str | Sequence[str] | None = "b7c2e4f1a903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE agent_instances SET configuration = "
        "((configuration)::jsonb - 'mcp_servers')::json"
    )


def downgrade() -> None:
    """The values are gone and nothing read them; there is nothing to put back."""
