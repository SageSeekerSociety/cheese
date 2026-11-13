"""A person has at most one pending request or invitation per team

``create_team_invitation`` and ``join`` checked for a pending row and then
inserted one, with nothing between the two: a double click on 邀请 (the dialog
had no in-flight guard) or two admins at once both passed the check and left
two pending invitations for the same person. After that the check itself
failed — it read the one row it expected and found two — so that person could
be neither invited nor let in again.

``uq_team_membership_application_pending`` holds the rule the check meant:
one PENDING, not deleted, row per (user, team), request and invitation alike
(the service already refused one while the other waited).

Duplicates already in a deployed database are resolved first: in each group the
earliest row stays and the rest become CANCELED, as if their sender had withdrawn
them, so nobody holds an invitation that disappears without a status. Run
again it finds nothing to do. The UPDATE sits in the same autocommit block as
the build, right before it, so the window in which the previous release can
insert a fresh duplicate is the build itself; a build that fails on one leaves
an invalid index, which ``env.py`` drops before the next try, and the rerun
resolves that duplicate too.

Built CONCURRENTLY: the serving backend writes this table on every invitation
and join request. A partial unique index cannot back a UNIQUE constraint, so
the index is the rule.

Revision ID: b7e2c4a91f30
Revises: a0f25891015d
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2c4a91f30"
down_revision: str | Sequence[str] | None = "a0f25891015d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "team_membership_application"
INDEX = "uq_team_membership_application_pending"
#: Same text as the model's ``postgresql_where`` (``app.domain.team.models``).
PENDING = "status = 'PENDING' AND deleted_at IS NULL"

RESOLVE_DUPLICATES = """
UPDATE team_membership_application AS a
SET status = 'CANCELED',
    processed_by_id = a.initiator_id,
    processed_at = now(),
    updated_at = now()
WHERE a.status = 'PENDING' AND a.deleted_at IS NULL
  AND EXISTS (
    SELECT 1 FROM team_membership_application AS b
    WHERE b.user_id = a.user_id AND b.team_id = a.team_id
      AND b.status = 'PENDING' AND b.deleted_at IS NULL
      AND (b.created_at, b.id) < (a.created_at, a.id)
  )
"""


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(RESOLVE_DUPLICATES)
        op.create_index(
            INDEX,
            TABLE,
            ["user_id", "team_id"],
            unique=True,
            postgresql_where=sa.text(PENDING),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX, table_name=TABLE, postgresql_concurrently=True, if_exists=True
        )
