"""project_skills / project_skill_revisions —— a project's saved ways of working

A saved skill is edited in place; every confirmed version is kept as a revision
so it can be read or restored, and only a confirmed revision ships to sessions.

Revision ID: 5c1d8e7f2b90
Revises: d4e7a2c91b35
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5c1d8e7f2b90"
down_revision: str | Sequence[str] | None = "d4e7a2c91b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("inputs", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.Text(), nullable=False),
        sa.Column("outputs", sa.Text(), nullable=False, server_default=""),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "source_topic_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "shipped_revision", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_project_skill_name"),
    )
    op.create_index("ix_project_skills_project_id", "project_skills", ["project_id"])
    op.create_table(
        "project_skill_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "skill_id",
            sa.Uuid(),
            sa.ForeignKey("project_skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_by", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("skill_id", "revision", name="uq_project_skill_revision"),
    )
    op.create_index(
        "ix_project_skill_revisions_skill_id", "project_skill_revisions", ["skill_id"]
    )


def downgrade() -> None:
    op.drop_table("project_skill_revisions")
    op.drop_table("project_skills")
