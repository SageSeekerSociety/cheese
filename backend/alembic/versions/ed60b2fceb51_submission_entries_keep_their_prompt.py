"""A submission entry keeps the name of the form item it answered.

Entries were titled by the task's form as it reads now, matched by position, so
reordering, renaming or removing form items relabelled every submission already
handed in. From here on the name is written with the entry.

Entries written before this were never told their name, and the form as it was
when they were submitted is not kept anywhere. The form as it reads now, at the
entry's position, is the only value left, and it is what these entries have
been shown under until today, so filling it in changes nothing a reader sees.
A blank name, or a position past the end of the form, stays NULL and the entry
is numbered as before.
"""

import sqlalchemy as sa

from alembic import op

revision = "ed60b2fceb51"
down_revision = "a3f70c5e9b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_submission_entry",
        sa.Column("prompt", sa.Text(), nullable=True),
    )
    op.execute(
        """
        UPDATE task_submission_entry AS e
        SET prompt = NULLIF(f.description, '')
        FROM task_submission AS s
        JOIN task_membership AS m ON m.id = s.membership_id
        JOIN task_submission_schema AS f ON f.task_id = m.task_id
        WHERE e.task_submission_id = s.id
          AND f.index = e.index
        """
    )


def downgrade() -> None:
    op.drop_column("task_submission_entry", "prompt")
