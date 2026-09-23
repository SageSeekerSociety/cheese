"""account unique indexes

Registration, OAuth account creation and passkey registration all read before
they write, so two concurrent requests could both pass the read and both
insert. Every later lookup by that value then finds two rows and fails. These
indexes make the database the judge.

The migration never rewrites or deletes data. If a database already holds
duplicates, ``upgrade`` stops before creating any index and names every
conflicting value with its row ids, so they can be resolved by hand first.

Revision ID: 514d7c9cb013
Revises: d4c1a7f83b96
Create Date: 2026-09-23 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "514d7c9cb013"
down_revision: str | Sequence[str] | None = "d4c1a7f83b96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (label, duplicate query). Each query mirrors one index below: same key
# expression, same partial condition.
_DUPLICATE_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "user lower(username), deleted_at IS NULL",
        """
        SELECT lower(username) AS value, array_agg(id ORDER BY id) AS ids
        FROM "user" WHERE deleted_at IS NULL
        GROUP BY lower(username) HAVING count(*) > 1
        """,
    ),
    (
        "user lower(email), deleted_at IS NULL",
        """
        SELECT lower(email) AS value, array_agg(id ORDER BY id) AS ids
        FROM "user" WHERE deleted_at IS NULL
        GROUP BY lower(email) HAVING count(*) > 1
        """,
    ),
    (
        "user_profile user_id, deleted_at IS NULL",
        """
        SELECT user_id::text AS value, array_agg(id ORDER BY id) AS ids
        FROM user_profile WHERE deleted_at IS NULL
        GROUP BY user_id HAVING count(*) > 1
        """,
    ),
    (
        "user_o_auth_connection (provider_id, provider_user_id)",
        """
        SELECT provider_id || ':' || provider_user_id AS value,
               array_agg(id ORDER BY id) AS ids
        FROM user_o_auth_connection
        GROUP BY provider_id, provider_user_id HAVING count(*) > 1
        """,
    ),
    (
        "passkey credential_id",
        """
        SELECT credential_id AS value, array_agg(id ORDER BY id) AS ids
        FROM passkey
        GROUP BY credential_id HAVING count(*) > 1
        """,
    ),
)


def find_duplicates(connection: sa.engine.Connection) -> list[str]:
    """One line per conflicting value: which index, the value, the row ids."""
    found: list[str] = []
    for label, query in _DUPLICATE_QUERIES:
        for value, ids in connection.execute(sa.text(query)):
            found.append(f"{label}: {value!r} -> ids {list(ids)}")
    return found


def upgrade() -> None:
    """Upgrade schema."""
    duplicates = find_duplicates(op.get_bind())
    if duplicates:
        raise RuntimeError(
            "Cannot create account unique indexes: duplicate rows exist. "
            "Resolve them by hand, then rerun the migration. Nothing was "
            "changed.\n  " + "\n  ".join(duplicates)
        )

    op.create_index(
        "uq_user_username_lower",
        "user",
        [sa.text("lower(username)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_user_email_lower",
        "user",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_user_profile_user_id",
        "user_profile",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_user_o_auth_connection_provider",
        "user_o_auth_connection",
        ["provider_id", "provider_user_id"],
        unique=True,
    )
    op.create_index(
        "uq_passkey_credential_id", "passkey", ["credential_id"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_passkey_credential_id", table_name="passkey")
    op.drop_index(
        "uq_user_o_auth_connection_provider", table_name="user_o_auth_connection"
    )
    op.drop_index("uq_user_profile_user_id", table_name="user_profile")
    op.drop_index("uq_user_email_lower", table_name="user")
    op.drop_index("uq_user_username_lower", table_name="user")
