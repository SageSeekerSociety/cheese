"""a course declares which modules it shows

The 配置页 half of the course template: a teacher turns a module off and it
leaves the sidebar and the course home — the address still works, the capability
is untouched. It lives on the 题目版 (`space`) because the modules are the
board's own screens, not any one project's: one course, one set of switches.

Additive only, with a server default of `{}` — and `{}` means "everything on"
(`app.domain.space.course_modules`), so every existing 题目版 keeps the screens
it has today and nothing needs backfilling.

Revision ID: c1a5e7d93b40
Revises: b7e4a1c9d0f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "c1a5e7d93b40"
down_revision: str | Sequence[str] | None = "b7e4a1c9d0f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "space",
        sa.Column("course_modules", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("space", "course_modules")
