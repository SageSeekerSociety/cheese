"""Index member spending without locking the existing usage table.

Revision ID: 7dba632a1e9f
Revises: 68c351ed9d4a
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7dba632a1e9f"
down_revision: str | Sequence[str] | None = "68c351ed9d4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_resource_usage_project_owner_created"


def upgrade() -> None:
    # Separate from the table migration so an interrupted concurrent build
    # resumes without replaying table creation already committed before it.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE resource_usage VALIDATE CONSTRAINT fk_resource_usage_conversation_id_conversations"
        )
        valid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT indisvalid FROM pg_index "
                    "WHERE indexrelid = to_regclass(:index)"
                ),
                {"index": _INDEX},
            )
            .scalar_one_or_none()
        )
        if valid is False:
            op.drop_index(_INDEX, postgresql_concurrently=True)
        op.create_index(
            _INDEX,
            "resource_usage",
            ["project_id", "credit_owner_handle", "created_at"],
            postgresql_include=["credits"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(_INDEX, postgresql_concurrently=True, if_exists=True)
