"""Agent session — where ONE agent's conversation in one place got to.

The third of the three layers the other two already name (see
:mod:`app.domain.agent_instance.models`): a **type** is 出厂设置 and belongs to no
project, an **instance** is that type working in one project and owns what it has
learned there, and a **session** is one conversation that may be thrown away.
Until now the third layer was a single ``topics.session_id`` column, which said —
structurally, not by policy — that a place hosts at most one agent.

Keyed by ``(where, agent_handle, harness)`` — where being a room's main line
(``task_id IS NULL``) or one thread in it — so a room can host several agents at
once, each keeps its own conversation, and a piece of work gets a fresh one
rather than inheriting whatever the room was in the middle of. ``agent_handle`` is
:attr:`~app.domain.agent_instance.services.ResolvedAgent.handle`, the same key the
agent's memory pool is named by — not the instance's uuid, because a project that
never configured an agent has no instance row at all and NULL does not compare
equal to NULL in a unique index. It is also not the authorship handle
(``cheese-<topic hex>``): that answers "who took this action", while this answers
"whose conversation is this".

One consequence worth stating: the implicit default and a later-configured
instance that uses the same harness and is called ``cheese`` share a key,
so configuring one inherits the conversation the project's 芝士 already had.
That is the same continuity-over-purity call ``ResolvedAgent`` already makes
for the memory pool.

Handing a topic to a different agent therefore destroys nothing — the new agent
looks up a key that has no row and starts fresh, and handing it back finds the
old row still there. A row is written only once a real session exists, so "this
topic has run" is exactly "a row exists for it".
"""

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AgentSession(UuidPk, Timestamps, Base):
    __tablename__ = "agent_sessions"
    # One session per agent per PLACE, and a place is a room's main line or one
    # thread in it. Two partial indexes rather than one over
    # (topic_id, task_id, agent_handle): `task_id` is NULL on every room row and
    # NULL is not equal to NULL in a unique index, so the wider index would let
    # a room grow a second session per agent without complaining.
    __table_args__ = (
        Index(
            "uq_agent_sessions_room",
            "topic_id",
            "agent_handle",
            "harness",
            unique=True,
            postgresql_where=text("task_id IS NULL"),
        ),
        Index(
            "uq_agent_sessions_thread",
            "task_id",
            "agent_handle",
            "harness",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    # THE ROOM. Always a room — a thread names its room here and itself below,
    # so anything that wants "where can a person read this" has one answer.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # Which thread in that room, NULL for the room's own main line. A thread
    # gets its own session because a clean context is most of what pulling work
    # out of the room was for.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # ResolvedAgent.handle — the agent's key inside its project.
    agent_handle: Mapped[str] = mapped_column(String(64))
    harness: Mapped[str] = mapped_column(
        String(64), default="claude-code", server_default="claude-code"
    )
    # What the harness resumes this conversation by. Opaque to the platform: it
    # is Claude Code's session id today and whatever the next harness hands back
    # tomorrow, so nothing here may parse it.
    resume_token: Mapped[str] = mapped_column(String(128))
