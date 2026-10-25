"""One live claim per member and task

Revision ID: 9e5088946704
Revises: 168348b68d6c
Create Date: 2026-10-25

#4767 §二. Claiming a 题目 the member (a person or a team) already holds now
answers with that claim instead of refusing it, and two claims of one member
at the same moment must not both land. The database holds that: at most one
claim of a member on a task that is live (not withdrawn) and not rejected —
a rejected application stays beside the one that reapplied, which is why the
index leaves rejected rows out, and why the release still serving (whose
reapply inserts beside a rejected row) is not refused by it.

Before the index, any duplicates a race already left are retired: of a
member's live, not rejected claims of one task the approved one stays (else
the newest), and the others are withdrawn (``deleted_at``), as a person would
withdraw them. Idempotent: a second run finds nothing to retire.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9e5088946704"
down_revision: str | Sequence[str] | None = "168348b68d6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "uq_task_membership_live_claim"
# The same predicate as the model's (`TaskMembership.__table_args__`).
WHERE = "deleted_at IS NULL AND approved <> 1"


def _invalid() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": NAME},
        )
        .scalar()
    )


def upgrade() -> None:
    op.execute(
        sa.text(f"""
            UPDATE task_membership AS m
            SET deleted_at = now(), updated_at = now()
            FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY task_id, member_id, is_team
                    ORDER BY (approved = 0) DESC, created_at DESC, id DESC
                ) AS rank
                FROM task_membership
                WHERE {WHERE}
            ) AS ranked
            WHERE m.id = ranked.id AND ranked.rank > 1
        """)
    )
    with op.get_context().autocommit_block():
        if _invalid():
            op.drop_index(
                NAME, table_name="task_membership", postgresql_concurrently=True
            )
        op.create_index(
            NAME,
            "task_membership",
            ["task_id", "member_id", "is_team"],
            unique=True,
            postgresql_where=sa.text(WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            NAME,
            table_name="task_membership",
            postgresql_concurrently=True,
            if_exists=True,
        )
