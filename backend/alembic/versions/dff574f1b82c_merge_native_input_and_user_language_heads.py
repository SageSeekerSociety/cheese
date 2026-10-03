"""merge native-input and user-language heads

Revision ID: dff574f1b82c
Revises: b74d81ac09f2, e5a91c3d7b40
Create Date: 2026-10-03
"""

from collections.abc import Sequence

revision: str = "dff574f1b82c"
down_revision: str | Sequence[str] | None = ("b74d81ac09f2", "e5a91c3d7b40")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
