"""merge ask and doc-comment heads

Revision ID: b7c41e0a93fd
Revises: dff574f1b82c, 1ca025b94a3e
Create Date: 2026-10-03
"""

from collections.abc import Sequence

revision: str = "b7c41e0a93fd"
down_revision: str | Sequence[str] | None = ("dff574f1b82c", "1ca025b94a3e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
