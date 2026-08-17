"""projects.external_task_id: the 赛题 a 2.0 project was created from

The 1.0 team-project already carries this idea; without it on the 2.0 project,
"create a project from this 赛题" produces something with no way back to the
赛题 it came from. Additive and nullable — a project made from the rail has none.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7f4a1b93e28"
down_revision: str | Sequence[str] | None = "b2c9d4e17a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("external_task_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        op.f("ix_projects_external_task_id"),
        "projects",
        ["external_task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_external_task_id"), table_name="projects")
    op.drop_column("projects", "external_task_id")
