"""agent uid range

Agents take their id from a sequence of their own, so an agent uid is
recognisable as one. See ``app.domain.identity.uids`` for why it is a range
rather than a column, and why it stops at int4's ceiling.

Only the sequence is created here — no table and no column changes. Existing
agent rows keep their ordinary ids on purpose: renumbering them would mean
touching every column that stores a user id, across roughly thirty tables, for
a property nothing is allowed to depend on.

Revision ID: a7f2c9e10b45
Revises: c4d81f6a27b3
Create Date: 2026-09-14 03:07:56.596033

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f2c9e10b45"
down_revision: str | Sequence[str] | None = "c4d81f6a27b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_UID_SEQUENCE = "agent_uid_seq"
# Kept in step with ``AGENT_UID_START`` in ``app.domain.identity.uids`` by the
# test that reads the sequence back (``test_agent_identity``); a migration has
# to stay runnable against an old checkout, so it does not import app code.
AGENT_UID_START = 2_000_000_000


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"CREATE SEQUENCE IF NOT EXISTS {AGENT_UID_SEQUENCE} "
        f"START WITH {AGENT_UID_START}"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"DROP SEQUENCE IF EXISTS {AGENT_UID_SEQUENCE}")
