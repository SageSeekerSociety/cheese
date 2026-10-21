"""Every reward pack lasts a year from when it was issued

Revision ID: e4a7c2d9b6f1
Revises: 53f2f3deeb83
Create Date: 2026-10-22

Reward packs (``compute_grants.source = 'reward'``) now share one lifetime,
whatever earned them: the beta claim, an accepted feedback report or an
invitation. Packs issued before this had no expiry (beta, feedback) or 90 days
(invitations); each is re-dated to 365 days after it was issued. Packs an
administrator granted by hand keep the expiry they were given.

The 365 days is ``REWARD_LIFETIME`` in ``app/domain/usage/incentives.py`` at the
time of writing, copied here because a migration must not import ``app``.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e4a7c2d9b6f1"
down_revision: str | Sequence[str] | None = "53f2f3deeb83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIFETIME = "interval '365 days'"


def upgrade() -> None:
    with_lock_retries("compute_grants")
    op.execute(
        f"UPDATE compute_grants SET expires_at = created_at + {_LIFETIME} "
        "WHERE source = 'reward' "
        f"AND expires_at IS DISTINCT FROM created_at + {_LIFETIME}"
    )


def downgrade() -> None:
    """Leaves every reward pack's one-year expiry in place: the expiries it
    replaced (none, or 90 days) are not recorded anywhere, and the previous
    release reads a year like any other expiry."""
