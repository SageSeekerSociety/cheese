"""rename the 1.0 project tables to team_project

#370: the 知是 team project and the cheesex AI workspace are unrelated resources
that were told apart by singular-vs-plural — `project` here, `projects` there.
The route moved to /team-projects in the same change; this moves the tables so
the schema stops carrying the trap the routes just shed.

Renames only. No column, constraint-shape or data change: a rename preserves
rows, primary keys and foreign keys, so this is reversible and cheap on a live
database. The indexes move with the tables because one of them
(`ix_project_name`) is auto-named by SQLAlchemy after the table — leaving it
behind would make the very next `alembic revision --autogenerate` want to
recreate it.

Revision ID: e7c2b41d90a5
Revises: c4a71e5d9b30
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7c2b41d90a5"
down_revision: str | Sequence[str] | None = "c4a71e5d9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = [
    ("project", "team_project"),
    ("project_membership", "team_project_membership"),
]

# (old, new) — every index the creating migration (41224effe32b) made.
_INDEXES = [
    ("ix_project_leader_id", "ix_team_project_leader_id"),
    ("ix_project_name", "ix_team_project_name"),
    ("ix_project_parent_id", "ix_team_project_parent_id"),
    ("ix_project_team_id", "ix_team_project_team_id"),
    ("ix_project_membership_project_id", "ix_team_project_membership_project_id"),
    ("ix_project_membership_user_id", "ix_team_project_membership_user_id"),
    ("uq_project_membership_project_user", "uq_team_project_membership_project_user"),
]


def _rename(pairs: list[tuple[str, str]], kind: str) -> None:
    # IF EXISTS so a database that predates one of these (or was built by a
    # future squashed baseline) is skipped rather than aborting the upgrade
    # halfway — a half-applied rename is the one outcome worth engineering away.
    for old, new in pairs:
        op.execute(f'ALTER {kind} IF EXISTS "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    _rename(_TABLES, "TABLE")
    _rename(_INDEXES, "INDEX")


def downgrade() -> None:
    _rename([(new, old) for old, new in _INDEXES], "INDEX")
    _rename([(new, old) for old, new in _TABLES], "TABLE")
