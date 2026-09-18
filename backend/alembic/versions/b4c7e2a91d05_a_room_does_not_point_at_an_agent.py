"""a room does not point at an agent: seat each room's former agent

Revision ID: b4c7e2a91d05
Revises: d3f1a7c52e08
Create Date: 2026-09-18 12:10:00

A room is a collaboration space. It seats members, several of which may be
agents, and which one answers a message is decided by who the message addresses.
`topics.agent_instance_id` said instead which ONE agent a room was handed to —
and every "which agent is this" question in the platform ended up reading it.

This moves that information to where the model keeps it: each room that pointed
at an agent gets that agent seated on its roster, under the agent's own identity
handle (the same derivation as `agent_instance_handle`), and the pointer is
cleared. The 29 rooms on dev that pointed at a teammate other than the project
default keep working with that teammate — it is now a member, addressed by name.

Private 1:1s keep the column: it is the DM's other party, until a DM with a
teammate records that as `private_peer` the way a DM with a person does.

Downgrade restores nothing, on purpose: the seat is not lost, and which seat had
been "the" pointer is not recoverable once a room may hold several.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4c7e2a91d05"
down_revision: str | Sequence[str] | None = "d3f1a7c52e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO topic_memberships (id, topic_id, member_handle, role, created_at, updated_at)
        SELECT gen_random_uuid(),
               t.id,
               'cheese-' || left(replace(t.agent_instance_id::text, '-', ''), 12),
               'member',
               now(),
               now()
        FROM topics t
        WHERE NOT t.is_private AND t.agent_instance_id IS NOT NULL
        ON CONFLICT (topic_id, member_handle) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE topics SET agent_instance_id = NULL
        WHERE NOT is_private AND agent_instance_id IS NOT NULL
        """
    )


def downgrade() -> None:
    pass
