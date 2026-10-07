"""Project slugs, and numbers for tasks, documents and channels

Revision ID: 5a00f11b4537
Revises: a4d8e2f61c07
Create Date: 2026-10-07

Addresses people can say (`app/domain/project/address.py`). Every existing
project gets a random eight-character slug; its tasks, documents of its own and
channels are numbered from 1 in the order they were made.
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5a00f11b4537"
down_revision: str | Sequence[str] | None = "a4d8e2f61c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

# Which rows of each table get a number, and the counter each one counts on.
_NUMBERED = {
    "tasks": ("tasks", "TRUE"),
    "topics": ("channels", "kind IN ('root', 'topic') AND NOT is_private"),
    "documents": (
        "docs",
        "id NOT IN (SELECT document_id FROM tasks WHERE document_id IS NOT NULL)"
        " AND id NOT IN (SELECT overview_document_id FROM projects"
        " WHERE overview_document_id IS NOT NULL)",
    ),
}


def upgrade() -> None:
    op.create_table(
        "project_slugs",
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("slug"),
    )
    op.create_index("ix_project_slugs_project_id", "project_slugs", ["project_id"])
    op.create_table(
        "project_counters",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "kind"),
    )

    op.add_column("projects", sa.Column("slug", sa.String(32), nullable=True))
    bind = op.get_bind()
    project_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM projects"))]
    used: set[str] = set()
    for project_id in project_ids:
        slug = ""
        while not slug or slug in used:
            slug = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        used.add(slug)
        bind.execute(
            sa.text("UPDATE projects SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": project_id},
        )
    op.alter_column("projects", "slug", nullable=False)
    op.create_unique_constraint("uq_projects_slug", "projects", ["slug"])

    for table, (kind, which) in _NUMBERED.items():
        op.add_column(table, sa.Column("number", sa.Integer(), nullable=True))
        bind.execute(
            sa.text(
                f"""
                UPDATE {table} AS t SET number = n.number
                FROM (
                    SELECT id, row_number() OVER (
                        PARTITION BY project_id ORDER BY created_at, id
                    ) AS number
                    FROM {table} WHERE {which}
                ) AS n
                WHERE t.id = n.id
                """
            )
        )
        bind.execute(
            sa.text(
                f"""
                INSERT INTO project_counters (project_id, kind, last_number)
                SELECT project_id, :kind, max(number) FROM {table}
                WHERE number IS NOT NULL GROUP BY project_id
                """
            ),
            {"kind": kind},
        )
        op.create_unique_constraint(
            f"uq_{table}_project_number", table, ["project_id", "number"]
        )


def downgrade() -> None:
    for table in _NUMBERED:
        op.drop_constraint(f"uq_{table}_project_number", table, type_="unique")
        op.drop_column(table, "number")
    op.drop_constraint("uq_projects_slug", "projects", type_="unique")
    op.drop_column("projects", "slug")
    op.drop_table("project_counters")
    op.drop_index("ix_project_slugs_project_id", table_name="project_slugs")
    op.drop_table("project_slugs")
