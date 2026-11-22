"""Validate the constraints of a person's attachment

Revision ID: d52f8a0c3e16
Revises: c41e7b9d2f05

The constraints ``c41e7b9d2f05`` added NOT VALID are validated, each its own
statement; ``environment_attachments.conversation_id`` is made NOT NULL
behind its validated CHECK in ``f1b6c4e8a9d3``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d52f8a0c3e16"
down_revision: str | Sequence[str] | None = "c41e7b9d2f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINTS = (
    "fk_environment_attachments_user_id_user",
    "fk_environment_attachments_conversation_id_conversations",
    "ck_environment_attachments_one_attacher",
    "ck_environment_attachments_conversation_id_not_null",
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name in _CONSTRAINTS:
            op.execute(
                f"ALTER TABLE environment_attachments VALIDATE CONSTRAINT {name}"
            )


def downgrade() -> None:
    raise RuntimeError("A person's attachments cannot be undone")
