"""A friend who gets started gives their invite seat back

Revision ID: d6a3f81c2e47
Revises: b7e4c9a21d68
Create Date: 2026-10-22

Each friend who accepts their first delivery within seven days of registering
(``invite_referrals.activated_at`` set) now adds one use to their inviter's own
code, so a code runs out only on friends who never get started. Codes gain one
use for every friend already activated on them, as if the rule had always held.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d6a3f81c2e47"
down_revision: str | Sequence[str] | None = "b7e4c9a21d68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("invite_code")
    op.execute(
        "UPDATE invite_code AS c SET max_uses = c.max_uses + started.n "
        "FROM (SELECT inviter_user_id, count(*) AS n FROM invite_referrals "
        "WHERE activated_at IS NOT NULL GROUP BY inviter_user_id) AS started "
        "WHERE c.owner_user_id = started.inviter_user_id"
    )


def downgrade() -> None:
    """Leaves the returned seats in place: friends may already have registered
    on them, and taking them back would leave codes with more uses than seats.
    The previous release reads a larger ``max_uses`` like any other."""
