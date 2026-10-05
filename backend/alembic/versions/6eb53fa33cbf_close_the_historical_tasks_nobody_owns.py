"""Close the historical tasks nobody owns

Revision ID: 6eb53fa33cbf
Revises: b6e2d94a1c37
Create Date: 2026-10-05

d7a419be028c kept each room's old work tree as a task (``historical_delivery``
holds the tree), open unless the tree had merged, with no owner. Since tasks
became conversations, only a task's owner may speak in one and an open task is
listed under its room, so these are conversations nobody can enter, listed in
every room that had a tree. They are closed; what they record stays.

A historical task that has an owner was taken up by someone and is left as it
is. Closing here removes no checkout: a closed task's checkout is
``tasks/<task id>`` on its machine, and f7985445d2bf gave these tasks new ids.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6eb53fa33cbf"
down_revision: str | Sequence[str] | None = "b6e2d94a1c37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE tasks
           SET status = 'closed', closed_at = now()
         WHERE status = 'open'
           AND historical_delivery IS NOT NULL
           AND owner_handle IS NULL
    """)


def downgrade() -> None:
    raise NotImplementedError("Which of them were open is not kept.")
