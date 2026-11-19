"""A lease whose sandbox was destroyed or parked says so itself

Revision ID: 2d06b1f1c765
Revises: b7d2e4f19a60

Until now one key on a session's ``execution_request`` carried two facts: that
its sandbox was destroyed (``sandbox_lost``) or parked (``sandbox_parked``), and
that its agent had not been told yet. Its lease kept ``status: ready`` and
named the machine it was on. From this release the lease itself says that
nothing answers on it (``status: released``), and the keys are only the notice
the agent is owed, so a person can bring the machine back for a task's files
without that notice being lost.

This marks the leases that are released today: those whose session still
carries either key. The previous release reads ``released`` as a lease that is
not ready, which is how it already treats a lost one (it checks the key), so
both releases read these rows the same way while they overlap. Idempotent: a
lease already marked is left alone.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2d06b1f1c765"
down_revision: str | Sequence[str] | None = "b7d2e4f19a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `app.domain.agent_session.models.RELEASED`, `LOST_KEY`, `PARKED_KEY`.
MARK_RELEASED = """
UPDATE agent_sessions
   SET work_lease = (
         (work_lease::jsonb - 'verified_at' - 'claim' - 'claim_until')
         || '{"status": "released"}'::jsonb
       )::json
 WHERE work_lease IS NOT NULL
   AND work_lease::jsonb ->> 'kind' = 'device'
   AND coalesce(work_lease::jsonb ->> 'status', 'ready') <> 'released'
   AND execution_request IS NOT NULL
   AND (execution_request::jsonb ? 'sandbox_lost'
        OR execution_request::jsonb ? 'sandbox_parked')
"""


def upgrade() -> None:
    op.execute(sa.text(MARK_RELEASED))


def downgrade() -> None:
    """The previous release reads a lost lease off its key, which is still
    set: the mark is harmless to it and nothing needs putting back."""
    pass
