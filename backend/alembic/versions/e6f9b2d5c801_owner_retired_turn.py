"""the takeover's retired predecessor, durable (FB-56 P2-2)

Revision ID: e6f9b2d5c801
Revises: d5e8a1c4b790
Create Date: 2026-10-01 06:30:00.000000

A takeover retires the previous owner's interval in the same transaction
that crowns the new one. The retired turn's id lands here in that
transaction, so the memory-side cleanup — the self-started hook state and
the runner marks — can name exactly that predecessor on any later replay
instead of scanning the topic and guessing (P2-2). NULL until a takeover
retires someone; the next takeover overwrites it with its own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f9b2d5c801"
down_revision: str | Sequence[str] | None = "d5e8a1c4b790"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_seat_owner",
        sa.Column("retired_turn_id", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_seat_owner", "retired_turn_id")
