"""fusion P4: cheesex Project.team_id → 知是 Team (native team→workspace link)

Revision ID: d1a2b3c4e5f6
Revises: c9f2a3b40e15
Create Date: 2026-07-11 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1a2b3c4e5f6"
down_revision: str | Sequence[str] | None = "c9f2a3b40e15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_projects_team_id", "projects", ["team_id"])
    op.create_foreign_key(
        "fk_projects_team_id",
        "projects",
        "team",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_team_id", "projects", type_="foreignkey")
    op.drop_index("ix_projects_team_id", table_name="projects")
    op.drop_column("projects", "team_id")
