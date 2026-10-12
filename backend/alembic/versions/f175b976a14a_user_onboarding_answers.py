"""the new-user guide: when it was done, and who the person said they are

Revision ID: f175b976a14a
Revises: a6c3e9d2f417
Create Date: 2026-10-11 12:00:00.000000

`onboarded_at` replaces `first_project_at`: the guide, not the first sign-in, now
makes the first project, and the column records that the guide was finished or
skipped. Accounts that already got a project keep their time. `first_project_at`
is no longer mapped but stays until a later migration drops it: the release this
deploy replaces still selects it while this runs. `work_role` and
`organization` are the guide's answers, read by every AI teammate on the roster.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f175b976a14a"
down_revision: str | Sequence[str] | None = "a6c3e9d2f417"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute('UPDATE "user" SET onboarded_at = first_project_at')
    op.add_column("user", sa.Column("work_role", sa.String(16), nullable=True))
    op.add_column("user", sa.Column("organization", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "organization")
    op.drop_column("user", "work_role")
    op.drop_column("user", "onboarded_at")
