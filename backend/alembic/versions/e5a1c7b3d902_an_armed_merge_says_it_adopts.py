"""accept_cards.auto_merge_adopts: a merge armed by 「提交并采纳」

A reviewer who hands a version in and adopts it in the same go arms its merge
on that head, as the 「检查通过后自动合并」 switch arms one. The two are told
apart on the card: the person did not turn auto-merge on, they adopted this
version, and the review column says so. Every merge armed before this was
armed by the switch, so the default is false.

Revision ID: e5a1c7b3d902
Revises: d81f3a6c2b95
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e5a1c7b3d902"
down_revision: str | Sequence[str] | None = "d81f3a6c2b95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("accept_cards")
    op.add_column(
        "accept_cards",
        sa.Column(
            "auto_merge_adopts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("accept_cards", "auto_merge_adopts")
