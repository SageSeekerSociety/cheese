"""The execution environment: a machine, the files on it and their lifecycle,
owned by nobody. A conversation points to its default one, and the agent
sessions working in it now are attached to it.

It sits below every package that asks which environment a conversation or a
session works in (``agent_session``, ``repository``, ``room_task``,
``memory``, ``machine``), so it imports none of them: a session or a sandbox
is named here by its id. ``machine`` backs an environment with a sandbox
(``sandbox_home_id``) and owns that sandbox's lifecycle.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

#: ``Environment.status`` while its executor is being installed, or while the
#: project environment it waits on is not ready yet.
PREPARING = "preparing"
#: ``Environment.status`` once its executor answers.
READY = "ready"
#: ``Environment.status`` once nothing on its machine answers for it any more:
#: its sandbox was destroyed (idle, or given up with its host) or parked with
#: its processes stopped. It still names where it was, and the sessions
#: attached to it stay attached until a placement brings it back.
RELEASED = "released"


class Environment(UuidPk, Timestamps, Base):
    """An execution environment: a machine, the files on it and their
    lifecycle. Owned by nobody: a conversation points to its default one
    (``ConversationEnvironment``), and the agent sessions working in it now
    are attached to it (``EnvironmentAttachment``).

    On a sandbox it names the sandbox's home (``sandbox_home_id``), whose
    lifecycle is the home's; a whole self-hosted machine has none, and one
    whose sandbox was destroyed has none until a placement brings one back.
    The lifecycle sweeps work on ``sandbox_homes`` only, so nothing they run
    can act on a row here.
    """

    __tablename__ = "environments"

    # The conversation whose machine it is (``holder.holder_of``): the room,
    # task or personal conversation its files belong to.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_environments_topic_id_conversations",
        ),
        index=True,
    )
    # None for one of a conversation in no project (``holder``).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_environments_project_id_projects",
        ),
        index=True,
        nullable=True,
    )
    # The machine its executor runs on, as its connector is known; None until
    # it is first placed on one. Not a key: an environment outlives the
    # binding of a person's computer, and is then one nothing can reach again
    # (``session_work._attempt``).
    device_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # The platform host it is on, when it is on one.
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "cloud_hosts.id",
            ondelete="SET NULL",
            name="fk_environments_host_id_cloud_hosts",
        ),
        index=True,
        nullable=True,
    )
    # The sandbox it is, when it is one and that sandbox is there.
    sandbox_home_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "sandbox_homes.id",
            ondelete="SET NULL",
            name="fk_environments_sandbox_home_id_sandbox_homes",
        ),
        index=True,
        nullable=True,
    )
    # Its directory's resource on the machine: the ``session-<resource>`` its
    # executor is reached at.
    resource_id: Mapped[str] = mapped_column(String(36))
    # The room generation it belongs to (``holder.generation``): a room's
    # cleanup removes the environments of the generation it was opened for.
    room_resource_id: Mapped[str] = mapped_column(String(36))
    # The choice it was made for, in the shape of
    # ``agent_environments.environment``: a sandbox of a spec, or a
    # self-hosted machine. Kept when its sandbox is destroyed: used again, it
    # is given a new one of the same choice.
    choice: Mapped[dict] = mapped_column(JSONB)
    # The executor installed in it, as the install left it: its home and state
    # directories, release, workspace, MCP servers, the URL it is reached at,
    # whether it is a sandbox of its own, and what its install has to say
    # (``environment_status``, ``root_snapshot_notice``). Nothing is queried
    # by these keys.
    executor: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # ``PREPARING``, ``READY`` or ``RELEASED``.
    status: Mapped[str] = mapped_column(
        String(16), default=PREPARING, server_default=PREPARING
    )
    # The install running in it: one at a time per environment. ``claim``
    # names the attempt; ``claim_until`` is when a claim nobody renewed lapses
    # (``lease_claim``).
    claim: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When its executor was last checked end to end; NULL while it is being
    # checked again, or once something says it may have changed.
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConversationEnvironment(Base):
    """The environment a conversation — a task, a channel, a personal
    conversation — works in by default: the machine is found from the
    conversation, never by picking one of its sessions.

    ``environment_id`` is NULL while it has none (never placed, or its last
    one was removed). ``left_host_id`` is the platform host it asked to leave
    (the spec it is on, asked for again): its next placement does not go
    straight back there, which the pool's preference for the fullest host
    would otherwise do.
    """

    __tablename__ = "conversation_environments"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="CASCADE",
            name="fk_conversation_environments_conversation_id_conversations",
        ),
        primary_key=True,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "environments.id",
            ondelete="SET NULL",
            name="fk_conversation_environments_environment_id_environments",
        ),
        index=True,
        nullable=True,
    )
    left_host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "cloud_hosts.id",
            ondelete="SET NULL",
            name="fk_conversation_environments_left_host_id_cloud_hosts",
        ),
        index=True,
        nullable=True,
    )


class EnvironmentAttachment(Base):
    """An agent session attached to an environment now: who a command there
    runs as. Its ``id`` is the name a command carries (``CHEESE_ATTACHMENT``)
    and its ``generation`` the one the session's credential is bound to (the
    token's ``lease`` claim), so attaching the session anew ends what its old
    credential may do there."""

    __tablename__ = "environment_attachments"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_environment_attachments_session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "environments.id",
            ondelete="CASCADE",
            name="fk_environment_attachments_environment_id_environments",
        ),
        index=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "agent_sessions.id",
            ondelete="CASCADE",
            name="fk_environment_attachments_session_id_agent_sessions",
        )
    )
    generation: Mapped[str] = mapped_column(String(36))
    # The digest of the launch values its executor was last checked against
    # (``session_work._setup_digest``): a change re-checks it.
    setup_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The session's last tool call here.
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
