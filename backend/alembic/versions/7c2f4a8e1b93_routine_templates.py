"""routine_templates —— a project's saved form presets

Revision ID: 7c2f4a8e1b93
Revises: 3e8b1d5a72c4

「存成模板」 收下来的那一条此前只存在这台浏览器里（一个项目一个 localStorage
键），于是同一个人在别的机器上看不到，项目里别的人也看不到。它搬到项目上：同一
个项目共用一份，跟着项目走。

Nothing is carried over from the browsers: a template is a few lines a person
typed into a form, and the list it sat in is not on this side of the wire. The
new table starts empty and fills as people tick 「存成模板」 from now on.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7c2f4a8e1b93"
down_revision: str | Sequence[str] | None = "3e8b1d5a72c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routine_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_routine_templates_project_id", "routine_templates", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("routine_templates")
