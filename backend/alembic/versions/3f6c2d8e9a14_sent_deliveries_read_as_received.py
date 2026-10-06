"""a delivery sent before deliveries had a state reads as received

Revision ID: 3f6c2d8e9a14
Revises: ed60b2fceb51
Create Date: 2026-10-06 08:00:00.000000

4d0e7a91c203 added ``deliveries.state`` with the server default ``pending`` and
backfilled nothing, so a notification already sent before that migration kept
its ``sent_at`` and read as ``pending`` for good. Every writer since sets
``sent_at`` together with ``received`` (``Ledger.mark_sent``), so these rows are
the only ones of their kind.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f6c2d8e9a14"
down_revision: str | Sequence[str] | None = "ed60b2fceb51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE deliveries SET state = 'received' "
        "WHERE sent_at IS NOT NULL AND state = 'pending'"
    )


def downgrade() -> None:
    # Which rows read as pending before is not recorded; received is their truth.
    pass
