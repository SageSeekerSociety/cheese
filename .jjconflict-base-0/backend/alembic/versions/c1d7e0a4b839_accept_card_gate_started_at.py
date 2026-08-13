"""accept_cards.gate_started_at — 闸门开跑打点 (pending_gate 孤儿卡, 2026-08-11)

Before this a gated card exposed only `created_at` and `gate_passed_at`, so the
whole middle was dark: a card sitting in `pending_gate` could not be told apart
from a card whose check was legitimately still running, and "how long has it
been running" was unanswerable. The column records the moment the check COMMAND
actually started (not the moment the card was filed — that would just duplicate
`created_at`), which splits the two failure shapes the sweeper has to act on:

  * `pending_gate` + `gate_started_at IS NULL` → the runner never got as far as
    running the check (task never scheduled, or资源/worktree preparation died).
  * `pending_gate` + `gate_started_at` long past → the check started and the
    process died under it, so nobody will ever call `finish_gate`.

Nullable with no backfill on purpose: for rows that predate this, "when did the
gate start" has no honest answer, and `NULL` says exactly that. The sweeper uses
`COALESCE(gate_started_at, created_at)` as its clock, so old rows still age out.

Revision ID: c1d7e0a4b839
Revises: b8e1d4c70a92
Create Date: 2026-08-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d7e0a4b839"
down_revision: str | Sequence[str] | None = "b8e1d4c70a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "accept_cards",
        sa.Column("gate_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("accept_cards", "gate_started_at")
