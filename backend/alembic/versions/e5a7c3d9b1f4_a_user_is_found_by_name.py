"""a user is found by name, and their profile by user, through an index

Every signed-in request reads its user by `username` (`"user".username = $1`),
and so do the roster's and the seat's "is this an agent" checks. The only
index on the column was `uq_user_username_lower`, over `lower(username)`,
which an equality on the bare column cannot use: each of those reads scanned
the whole table. Likewise the reads that join a user to their profile
(`user_profile.user_id = "user".id`, the project's member list among them)
name no `deleted_at`, so the partial `uq_user_profile_user_id` cannot serve
them and they scanned `user_profile`.

This adds a plain btree on each column. Not unique: uniqueness stays with the
partial indexes, among live rows only. CONCURRENTLY, because the release
serving while this runs keeps inserting users.

Revision ID: e5a7c3d9b1f4
Revises: c4d8e2a1f6b3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a7c3d9b1f4"
down_revision: str | Sequence[str] | None = "c4d8e2a1f6b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    ("ix_user_username", "user", "username"),
    ("ix_user_profile_user_id", "user_profile", "user_id"),
)


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, column in INDEXES:
            if _invalid(name):
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(
                name,
                table,
                [column],
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _ in INDEXES:
            op.drop_index(
                name, table_name=table, postgresql_concurrently=True, if_exists=True
            )
