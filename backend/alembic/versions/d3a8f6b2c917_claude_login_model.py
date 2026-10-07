"""The model a machine's own Claude Code calls on a model service

Revision ID: d3a8f6b2c917
Revises: c5e2a7d91f3b
Create Date: 2026-10-07

``device_claude_login.model``: the model a machine's owner pointed their own
Claude Code at, when it calls another model service (GLM, Kimi, a relay)
instead of a Claude account, so the devices page can say which.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3a8f6b2c917"
down_revision: str | Sequence[str] | None = "c5e2a7d91f3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "device_claude_login",
        sa.Column("model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_claude_login", "model")
