"""A forge token can be one that only reads

Revision ID: c5e2a7d91f3b
Revises: a6e2f91c4d07
Create Date: 2026-10-07

``forge_tokens.read_only``: a token handed to a session whose work is not kept
(a 支线, a task not yet started), which reads the repository and writes
nothing. On Forgejo it is a scoped access token the platform revokes when it
expires; the OAuth tokens beside it stay the ones every other session gets.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e2a7d91f3b"
down_revision: str | Sequence[str] | None = "a6e2f91c4d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "forge_tokens",
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("forge_tokens", "read_only")
