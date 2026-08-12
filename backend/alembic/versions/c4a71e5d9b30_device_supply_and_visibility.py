"""device.supply + device.visibility — 算力四轴的供给形式与可见性 (#282 决定 2)

`compute_profile` named one thing (how big) while deciding four (how big, what a
turn can see, how long it lives, whether it can be replaced). Two of those had no
name at all. This adds the two that belong on a device.

**supply** — how the machine came to be ours. Platform-provisioned (`cloud`) means
the platform may destroy it; human-enrolled (`self_hosted`) means it may only stop
using it. The same physical VM is either, depending on which door it came through
— 入口决定待遇，不是硬件决定待遇 — which is why MicroCloud needs no special case.

This fact is already *derivable* today: `project_machines.device_id` is non-null
exactly for the machines the platform opened. Deriving it is the bug. A semantics
that exists only as a reverse lookup cannot be read at the point of use, cannot be
constrained, and quietly acquires exceptions; the backfill below turns that last
reverse lookup into stored data, once.

**visibility** — what a turn on the machine can see and touch. Every device screen
today is `host` (it runs `claude --dangerously-skip-permissions` as the owner), so
the backfill is the server_default. `isolated` has no transport behind it yet; the
column exists so that transport is a new VALUE rather than a new column.

Both defaults are the CONSERVATIVE reading, not the common one: a row this
migration somehow fails to classify reads as "someone else's machine, visible to
everything" — under which the platform destroys nothing.

Revision ID: c4a71e5d9b30
Revises: b8e1d4c70a92
Create Date: 2026-08-12 00:00:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a71e5d9b30"
down_revision: str | Sequence[str] | None = "c1f7a3b90d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.device_supply_and_visibility")


def upgrade() -> None:
    op.add_column(
        "device",
        sa.Column(
            "supply",
            sa.String(length=16),
            nullable=False,
            server_default="self_hosted",
        ),
    )
    op.add_column(
        "device",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="host",
        ),
    )
    # The one-time backfill: every device a `project_machines` row points at was
    # enrolled BY the platform (`MachineService.enroll`), so it is cloud supply.
    # After this, nothing reads that join for meaning again — `check-repo-rules.sh`
    # fails the build if a new caller tries.
    result = op.get_bind().execute(
        sa.text(
            "UPDATE device SET supply = 'cloud' WHERE device_id IN "
            "(SELECT device_id FROM project_machines WHERE device_id IS NOT NULL)"
        )
    )
    logger.info("backfilled supply=cloud on %s device row(s)", result.rowcount)


def downgrade() -> None:
    op.drop_column("device", "visibility")
    op.drop_column("device", "supply")
