"""A task's teammate is named by its seat

Revision ID: b7e2d41c9a06
Revises: 8201771931c9
Create Date: 2026-10-07

`tasks.agent_handle` says which AI teammate works a task. It was written in
two shapes: the teammate's own handle (`agent_instances.handle`, such as
`cheese`), which the rooms turned into tasks carried over, and its seat on
rosters (`cheese-` and the first twelve hex digits of its id), which the task
page and the document agent read. The code that decides who answers in a task
read only the first shape and refused the second outright, so a task holding
a seat could not start a turn.

A task's teammate is now always its seat. Each value becomes the seat of the
teammate of the task's project that it names, by seat or by handle; a value
that names no teammate of that project (one since deleted, or an id from
before teammates were saved) is cleared, which hands the task to its
channel's teammate, as for a task never given one.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7e2d41c9a06"
down_revision: str | Sequence[str] | None = "8201771931c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    name_by_seat()


def name_by_seat() -> None:
    """Rewrite every task's teammate as the seat of the one it names."""
    op.execute("""
        UPDATE tasks t
        SET agent_handle = (
            SELECT 'cheese-' || left(replace(i.id::text, '-', ''), 12)
            FROM agent_instances i
            WHERE i.project_id = t.project_id
              AND (
                'cheese-' || left(replace(i.id::text, '-', ''), 12) = t.agent_handle
                OR i.handle = t.agent_handle
              )
            ORDER BY ('cheese-' || left(replace(i.id::text, '-', ''), 12)
                      = t.agent_handle) DESC
            LIMIT 1
        )
        WHERE t.agent_handle IS NOT NULL
    """)


def downgrade() -> None:
    """The handles it replaced are not kept: a seat names the same teammate."""
    pass
