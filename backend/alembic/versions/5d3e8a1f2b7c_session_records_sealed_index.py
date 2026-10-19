"""Session records: the expiry index covers every sealed run.

Archive runs (`archive/user-messages/`) are no longer written: transcripts are
kept in the bucket for good and only rows expire (#4398, Zhifei 2026-10-19), so
the index no longer leaves them out. The live predicate is the one
`app.domain.session_record.retention.expire` filters on.

Built CONCURRENTLY: the sealer and every session's drain write this table. An
invalid index left by a failed concurrent build is dropped before the retry.
"""

import sqlalchemy as sa

from alembic import op

revision = "5d3e8a1f2b7c"
down_revision = "c7cfcf6cbdc8"
branch_labels = None
depends_on = None

NAME = "ix_session_records_sealed"
OLD = "ix_session_records_expiring"


def _invalid() -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": NAME},
        )
        .scalar()
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        if _invalid():
            op.drop_index(
                NAME, table_name="session_records", postgresql_concurrently=True
            )
        op.create_index(
            NAME,
            "session_records",
            ["last_at"],
            postgresql_where=sa.text("object_key IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            OLD,
            table_name="session_records",
            postgresql_concurrently=True,
            if_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            OLD,
            "session_records",
            ["last_at"],
            postgresql_where=sa.text(
                "object_key IS NOT NULL AND NOT starts_with(object_key, 'archive/')"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            NAME,
            table_name="session_records",
            postgresql_concurrently=True,
            if_exists=True,
        )
