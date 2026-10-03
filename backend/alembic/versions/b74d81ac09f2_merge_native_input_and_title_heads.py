"""merge native-input and main's task-title heads

Revision ID: b74d81ac09f2
Revises: 72ee6f75d0dc, f9be82f700e2
Create Date: 2026-10-03
"""

from collections.abc import Sequence

revision: str = "b74d81ac09f2"
down_revision: str | Sequence[str] | None = ("72ee6f75d0dc", "f9be82f700e2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
