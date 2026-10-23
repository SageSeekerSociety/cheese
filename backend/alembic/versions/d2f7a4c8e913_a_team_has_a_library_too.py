"""library_files.team_id: a team has a library too

Revision ID: d2f7a4c8e913
Revises: b2f6d18c9a73

A library belongs to a project or to a team, and a person's own is their
personal team's. A row is a project's (``project_id``) or a team's
(``team_id``), never both and never neither. The partial unique index that
keeps one current file per name holds for a project's rows only: a team's
have no project, and nulls are distinct in it, so a team's get their own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "d2f7a4c8e913"
down_revision: str | Sequence[str] | None = "b2f6d18c9a73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEAM_INDEX = "ix_library_files_team_id"
TEAM_CURRENT = "uq_library_files_team_current"


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
    with_lock_retries("library_files, team")
    op.add_column("library_files", sa.Column("team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_library_files_team_id_team",
        "library_files",
        "team",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_library_files_one_shelf",
        "library_files",
        "num_nonnulls(project_id, team_id) = 1",
        postgresql_not_valid=True,
    )
    op.alter_column("library_files", "project_id", nullable=True)
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE library_files VALIDATE CONSTRAINT fk_library_files_team_id_team"
        )
        op.execute(
            "ALTER TABLE library_files VALIDATE CONSTRAINT ck_library_files_one_shelf"
        )
        for name in (TEAM_INDEX, TEAM_CURRENT):
            if _invalid(name):
                op.drop_index(
                    name, table_name="library_files", postgresql_concurrently=True
                )
        op.create_index(
            TEAM_INDEX,
            "library_files",
            ["team_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            TEAM_CURRENT,
            "library_files",
            ["team_id", "name"],
            unique=True,
            postgresql_where=sa.text("superseded_at IS NULL AND team_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    op.execute("DELETE FROM library_files WHERE team_id IS NOT NULL")
    op.drop_constraint("ck_library_files_one_shelf", "library_files", type_="check")
    op.drop_index(TEAM_CURRENT, table_name="library_files")
    op.alter_column("library_files", "project_id", nullable=False)
    op.drop_index(TEAM_INDEX, table_name="library_files")
    op.drop_constraint(
        "fk_library_files_team_id_team", "library_files", type_="foreignkey"
    )
    op.drop_column("library_files", "team_id")
