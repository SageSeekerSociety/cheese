"""fusion unify P2: retire the dead int `project` table (cheesex `projects` is the only project)  # noqa: E501

Revision ID: c9f2a3b40e15
Revises: b8e1c0a5f7d2
Create Date: 2026-07-11 21:20:00.000000

Main-cheese's `project` (int BigInteger) + `project_membership` were a gantt-style
team sub-feature that was never ported to the Python backend: no ORM model, no
service, no route. Its only would-be consumer (notification's project entity
resolver) calls a `get_projects_by_ids` that isn't even defined — dead on both
ends. cheesex's `projects` (uuid) is the one live project entity, so we drop the
dead pair. `knowledge.project_id` is a bare nullable int (no FK); it's left in
place, harmless.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9f2a3b40e15"
down_revision: str | Sequence[str] | None = "b8e1c0a5f7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("project_membership")
    op.drop_table("project")


def downgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.BigInteger(), sa.Sequence("project_seq"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("color_code", sa.String(7), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("leader_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("external_task_id", sa.BigInteger(), nullable=True),
        sa.Column("github_repo", sa.String(255), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "project_membership",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("project_membership_seq"),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("notes", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
