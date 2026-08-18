"""a session belongs to an agent, not to a room

``topics.session_id`` was one column, so a place could hold one conversation, so
a place could hold one agent. That was never a decision anybody made — it is what
a single column means, and the comment on it said as much ("phase one keeps at
most one agent per topic, which is why session_id can stay a single column").

``agent_sessions`` is that column keyed by ``(topic_id, agent_handle)`` instead.
The handle is ``ResolvedAgent.handle`` — the agent's key inside its project, the
same one its memory pool is named by — so the backfill has to reproduce how that
resolves: the topic's own instance, else the project's default, else the implicit
``cheese``.

Nothing is lost and nothing is invented: every topic that had a session gets
exactly one row, under the agent that was working there. What changes is that
handing a room to a different agent stops destroying anything — the new agent
looks up a key with no row and starts fresh, the old row stays where it is, and
handing the room back finds it again.

Revision ID: b3e91d47c05a
Revises: d5a2f70c9b18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e91d47c05a"
down_revision: str | Sequence[str] | None = "d5a2f70c9b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHEESE_HANDLE = "cheese"


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=64), nullable=False),
        sa.Column("resume_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "agent_handle", name="uq_agent_session_topic"),
    )
    op.create_index(op.f("ix_agent_sessions_topic_id"), "agent_sessions", ["topic_id"])

    # The same resolution `AgentInstanceService.for_topic` does, in SQL: the
    # topic's own instance, else the project's default, else the implicit 芝士.
    # Getting this wrong would not fail — it would file a live conversation under
    # a handle nobody looks up, which reads from the room as amnesia.
    op.execute(
        sa.text(
            """
            INSERT INTO agent_sessions
                (id, topic_id, agent_handle, resume_token, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                t.id,
                COALESCE(
                    own.handle,
                    dflt.handle,
                    :cheese
                ),
                t.session_id,
                now(),
                now()
            FROM topics t
            LEFT JOIN agent_instances own ON own.id = t.agent_instance_id
            LEFT JOIN projects p ON p.id = t.project_id
            LEFT JOIN agent_instances dflt ON dflt.id = p.default_agent_instance_id
            WHERE t.session_id IS NOT NULL
            """
        ).bindparams(cheese=CHEESE_HANDLE)
    )

    op.drop_column("topics", "session_id")


def downgrade() -> None:
    op.add_column(
        "topics", sa.Column("session_id", sa.String(length=128), nullable=True)
    )
    # A room can hold several sessions now and the column can hold one, so the
    # downgrade keeps the most recently written and drops the rest. Stated rather
    # than silently picked: there is no lossless way back.
    op.execute(
        sa.text(
            """
            UPDATE topics t
            SET session_id = s.resume_token
            FROM (
                SELECT DISTINCT ON (topic_id) topic_id, resume_token
                FROM agent_sessions
                ORDER BY topic_id, updated_at DESC
            ) s
            WHERE s.topic_id = t.id
            """
        )
    )
    op.drop_index(op.f("ix_agent_sessions_topic_id"), table_name="agent_sessions")
    op.drop_table("agent_sessions")
