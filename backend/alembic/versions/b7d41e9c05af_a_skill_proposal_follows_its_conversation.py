"""A skill proposal belongs to the conversation it was raised in

The card that asks a person to save a proposal is drawn in the conversation that
holds the teammate who raised it, and the line about it is a block in that same
conversation — but `project_skills.source_topic_id` pointed at `topics`, and a
task or a 支线 has no row there. So every proposal landed on the channel: the
line in the channel's main line, and the card visible only there, while the task
it was raised in showed nothing.

`source_topic_id` now points at `conversations`, the way
`feedback_proposal_dismissals.topic_id` already does (same reasoning, same
column-keeps-its-name). Existing values are room ids, which are conversation ids
too, so nothing is rewritten; a room is still one kind of conversation. Rows
whose value is not a conversation keep it and the key simply does not cover them
until the next write (there are none: every skill route has always written a
room id, and every room since `f7985445d2bf` is registered in `conversations`).

Revision ID: b7d41e9c05af
Revises: a3c9e15f7b20
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7d41e9c05af"
down_revision: str | Sequence[str] | None = "a3c9e15f7b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "project_skills_source_topic_id_fkey"
NEW = "fk_project_skills_source_topic_id_conversations"


def upgrade() -> None:
    with_lock_retries("project_skills, topics, conversations")
    op.drop_constraint(OLD, "project_skills", type_="foreignkey")
    op.create_foreign_key(
        NEW,
        "project_skills",
        "conversations",
        ["source_topic_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE project_skills VALIDATE CONSTRAINT {NEW}")


def downgrade() -> None:
    # A proposal raised in a task or a 支线 names a conversation `topics` does
    # not hold: the old shape cannot keep it, so it goes rather than leaving the
    # downgrade stuck on the foreign key.
    op.execute(
        "DELETE FROM project_skills s"
        " WHERE s.source_topic_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM topics t WHERE t.id = s.source_topic_id)"
    )
    with_lock_retries("project_skills, conversations, topics")
    op.drop_constraint(NEW, "project_skills", type_="foreignkey")
    op.create_foreign_key(
        OLD,
        "project_skills",
        "topics",
        ["source_topic_id"],
        ["id"],
        ondelete="SET NULL",
        postgresql_not_valid=True,
    )
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE project_skills VALIDATE CONSTRAINT {OLD}")
