"""An attachment names its conversation

Revision ID: f1b6c4e8a9d3
Revises: d52f8a0c3e16

``environment_attachments.conversation_id`` becomes NOT NULL: its CHECK was
validated in ``d52f8a0c3e16``, so no scan is made under the lock, and the
CHECK goes.
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "f1b6c4e8a9d3"
down_revision: str | Sequence[str] | None = "d52f8a0c3e16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("environment_attachments")
    op.execute(
        "ALTER TABLE environment_attachments ALTER COLUMN conversation_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE environment_attachments"
        " DROP CONSTRAINT ck_environment_attachments_conversation_id_not_null"
    )


def downgrade() -> None:
    raise RuntimeError("A person's attachments cannot be undone")
