"""Index a person's attachments

Revision ID: e63a9b1d4f27
Revises: f1b6c4e8a9d3

A person is attached to an environment at most once, and an attachment is
found by its person and by its conversation.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e63a9b1d4f27"
down_revision: str | Sequence[str] | None = "f1b6c4e8a9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_environment_attachments_user_id",
            "environment_attachments",
            ["user_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_environment_attachments_conversation_id",
            "environment_attachments",
            ["conversation_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "uq_environment_attachments_environment_id_user_id",
            "environment_attachments",
            ["environment_id", "user_id"],
            unique=True,
            postgresql_where="user_id IS NOT NULL",
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in (
            "uq_environment_attachments_environment_id_user_id",
            "ix_environment_attachments_conversation_id",
            "ix_environment_attachments_user_id",
        ):
            op.drop_index(
                name,
                table_name="environment_attachments",
                postgresql_concurrently=True,
                if_exists=True,
            )
