"""A 题目 has a library, and its content has versions

Revision ID: 0f147393ad6a
Revises: b7d41e9c05af
Create Date: 2026-10-24

#4767 §一. A library belongs to a project, a team, or now a 题目
(``library_files.task_id``): the files its publisher hands the takers. Exactly
one of the three is set, and a 题目's rows get their own partial unique index,
like a team's: the other two never see a row whose column of theirs is null.

``task_package_revisions`` numbers the versions of what a 题目 hands its
takers (description, what to hand in, 评分要点, files), each saying what
changed, and ``task_submission.package_revision`` records which one a
submission was made under.

The column and constraints go onto the live tables without scanning them:
``NOT VALID`` first, ``VALIDATE`` and the indexes outside the transaction that
added them. The previous release still serving writes rows with ``task_id``
null and one of the other two set, which the new check accepts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0f147393ad6a"
down_revision: str | Sequence[str] | None = "b7d41e9c05af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_INDEX = "ix_library_files_task_id"
TASK_CURRENT = "uq_library_files_task_current"


def _invalid(name: str) -> bool:
    """A concurrent build that failed leaves the index behind, marked invalid."""
    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT NOT indisvalid FROM pg_index "
                "WHERE indexrelid = to_regclass(:name)"
            ),
            {"name": name},
        )
    )


def upgrade() -> None:
    with_lock_retries("library_files, task, task_submission")
    op.add_column("library_files", sa.Column("task_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_library_files_task_id_task",
        "library_files",
        "task",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.drop_constraint("ck_library_files_one_shelf", "library_files", type_="check")
    op.create_check_constraint(
        "ck_library_files_one_shelf",
        "library_files",
        "num_nonnulls(project_id, team_id, task_id) = 1",
        postgresql_not_valid=True,
    )
    op.add_column(
        "task_submission",
        sa.Column("package_revision", sa.Integer(), nullable=True),
    )
    op.create_table(
        "task_package_revisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            ondelete="CASCADE",
            name="fk_task_package_revisions_task_id_task",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_task_package_revisions_task_id_number",
        "task_package_revisions",
        ["task_id", "number"],
        unique=True,
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE library_files VALIDATE CONSTRAINT fk_library_files_task_id_task"
        )
        op.execute(
            "ALTER TABLE library_files VALIDATE CONSTRAINT ck_library_files_one_shelf"
        )
        for name in (TASK_INDEX, TASK_CURRENT):
            if _invalid(name):
                op.drop_index(
                    name, table_name="library_files", postgresql_concurrently=True
                )
        op.create_index(
            TASK_INDEX,
            "library_files",
            ["task_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            TASK_CURRENT,
            "library_files",
            ["task_id", "name"],
            unique=True,
            postgresql_where=sa.text("superseded_at IS NULL AND task_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.execute("DELETE FROM library_files WHERE task_id IS NOT NULL")
    op.drop_index(TASK_CURRENT, table_name="library_files")
    op.drop_index(TASK_INDEX, table_name="library_files")
    op.drop_constraint("ck_library_files_one_shelf", "library_files", type_="check")
    op.create_check_constraint(
        "ck_library_files_one_shelf",
        "library_files",
        "num_nonnulls(project_id, team_id) = 1",
    )
    op.drop_constraint(
        "fk_library_files_task_id_task", "library_files", type_="foreignkey"
    )
    op.drop_column("library_files", "task_id")
    op.drop_table("task_package_revisions")
    op.drop_column("task_submission", "package_revision")
