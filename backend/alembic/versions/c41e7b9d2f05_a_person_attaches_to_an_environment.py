"""A person attaches to an environment

Revision ID: c41e7b9d2f05
Revises: ac5501d67db3

An attachment is an agent session's or a person's: ``session_id`` becomes
nullable beside a new ``user_id``, and exactly one of the two is named. Every
attachment names the conversation its attacher works in through the
environment (``conversation_id``); an agent's is its session's, filled here for
the rows already there. An environment records why the last bring-up somebody
asked for did not finish (``failure``) and until when one is under way
(``preparing_until``).

The previous release keeps inserting agent attachments without a
conversation while it still serves; a trigger fills it from the session, so
the column can be made NOT NULL in this release. The next release drops the
trigger. The constraints are added NOT VALID here and validated in
``d52f8a0c3e16``; the column is made NOT NULL in ``f1b6c4e8a9d3`` and the
indexes are built in ``e63a9b1d4f27``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c41e7b9d2f05"
down_revision: str | Sequence[str] | None = "ac5501d67db3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries('environment_attachments, environments, "user", conversations')
    op.alter_column(
        "environment_attachments", "session_id", existing_type=sa.Uuid(), nullable=True
    )
    op.add_column(
        "environment_attachments", sa.Column("user_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "environment_attachments",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_environment_attachments_user_id_user",
        "environment_attachments",
        "user",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_environment_attachments_conversation_id_conversations",
        "environment_attachments",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_environment_attachments_one_attacher",
        "environment_attachments",
        "num_nonnulls(session_id, user_id) = 1",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_environment_attachments_conversation_id_not_null",
        "environment_attachments",
        "conversation_id IS NOT NULL",
        postgresql_not_valid=True,
    )
    op.add_column("environments", sa.Column("failure", sa.String(32), nullable=True))
    op.add_column(
        "environments",
        sa.Column("preparing_until", sa.DateTime(timezone=True), nullable=True),
    )
    # The previous release's inserts name no conversation: its session's.
    op.execute("""
        CREATE FUNCTION environment_attachment_conversation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.conversation_id IS NULL AND NEW.session_id IS NOT NULL THEN
                SELECT conversation_id INTO NEW.conversation_id
                FROM agent_sessions WHERE id = NEW.session_id;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER environment_attachment_conversation
        BEFORE INSERT ON environment_attachments
        FOR EACH ROW EXECUTE FUNCTION environment_attachment_conversation();
    """)
    op.execute("""
        UPDATE environment_attachments AS a
        SET conversation_id = s.conversation_id
        FROM agent_sessions AS s
        WHERE s.id = a.session_id AND a.conversation_id IS NULL
    """)


def downgrade() -> None:
    raise RuntimeError("A person's attachments cannot be undone")
