"""a DM names its teammate as its peer, and the room pointer is gone

Revision ID: e7d2b91a4c06
Revises: b4c7e2a91d05
Create Date: 2026-09-19 07:30:00

A private 1:1 with an AI teammate is a two-member private room, exactly like a
1:1 with a person: the human is `private_owner`, the teammate's seat is
`private_peer`. Until now the teammate was the last thing `topics.agent_instance_id`
still said, with NULL meaning "whoever the project's default is right now".

Every agent DM gets its peer written down here, once:
  - a DM that named a teammate gets that teammate's seat;
  - a DM that named none gets the project's default's seat — the teammate that
    has been answering it — so moving the default later does not move it;
  - a DM in a project that has no saved default to name keeps its room-derived
    seat as the peer; the platform answers it with the default, as before.
Each named seat is also added to the DM's roster (the room-derived seat that
was there stays: its messages are on the wall and are labelled through it).

Then the column goes. Downgrade restores nothing: the pointer's information now
lives in `private_peer`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7d2b91a4c06"
down_revision: str | Sequence[str] | None = "c7d2e4f60a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEAT = "'cheese-' || left(replace({col}::text, '-', ''), 12)"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE topics t
           SET private_peer = {SEAT.format(col="coalesce(t.agent_instance_id, p.default_agent_instance_id)")}
          FROM projects p
         WHERE p.id = t.project_id
           AND t.is_private
           AND t.private_peer IS NULL
           AND coalesce(t.agent_instance_id, p.default_agent_instance_id) IS NOT NULL
        """
    )
    op.execute(
        f"""
        UPDATE topics t
           SET private_peer = {SEAT.format(col="t.id")}
         WHERE t.is_private AND t.private_peer IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(), t.id, t.private_peer, 'member', now(), now()
          FROM topics t
         WHERE t.is_private AND t.private_peer IS NOT NULL
        ON CONFLICT (topic_id, member_handle) DO NOTHING
        """
    )
    op.drop_constraint("fk_topics_agent_instance_id", "topics", type_="foreignkey")
    op.drop_index(op.f("ix_topics_agent_instance_id"), table_name="topics")
    op.drop_column("topics", "agent_instance_id")


def downgrade() -> None:
    pass
