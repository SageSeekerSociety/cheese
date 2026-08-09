"""Merge heads before pr accept fields.

Revision ID: 093133add3e1
Revises: e1f2a3b4c5d6, e3a94c6f5b18
Create Date: 2026-08-09 13:19:03.894201
"""

from collections.abc import Sequence

revision: str = "093133add3e1"
down_revision: str | Sequence[str] | None = ("e1f2a3b4c5d6", "e3a94c6f5b18")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
