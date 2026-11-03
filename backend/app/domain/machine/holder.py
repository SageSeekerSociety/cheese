"""Who a sandbox is for: its holder.

A sandbox and the slot it takes, the run it is charged as and the lock a tool
call takes before it lands there are the platform's own machinery, the same
for every sandbox. What differs is whose the sandbox is, and what follows
from that: who may have one started, who pays for it, which row a tool call
locks to order itself against the holder's own changes (an archival, say),
which environment a session works in, whether the holder is at work, what
credential its sessions work the sandbox with, and under which name the
sandbox's directories sit on the machine. Those answers are asked here and
nowhere else, so that
placement (``services``), the meter (``metering``), the idle sweep
(``lifecycle``, ``cloud_vm``) and the session's own admission
(``session_work``) never look a project or a room up for themselves.

There are two holders. A room (``Room``): every session in a channel, and in
each of its tasks and 支线, works in a sandbox of the room's, on the room's
project, paid for by the project's team. And a person's own 芝士 conversation
(``Personal``), one topic of their thread with 芝士: its sandbox is in no
project, only that person may have it started, and their own credits pay for
it. A holder is named by the conversation a sandbox's row points at
(``SandboxHome.topic_id``), and which of the two it is is the register's kind
(``conversations``).
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.request_facts import recall, remember
from app.core.sandbox_auth import (
    bind_resource_token,
    mint_personal_execution_credential,
)
from app.core.sandbox_auth import personal_claims as personal_claims_of
from app.core.sentences import say
from app.domain.agent.environment import STANDARD, Sandbox, SelfHosted
from app.domain.agent.harness import PI
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.adapters import project_team
from app.domain.agent_session.models import AgentSession
from app.domain.assistant.models import AssistantConversation
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.conversation.services import of_rooms, room_column, room_of
from app.domain.identity.actor import Actor
from app.domain.machine.environments import environment_of
from app.domain.machine.models import SandboxHome
from app.domain.membership.roster import roster
from app.domain.project.services import ProjectService
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic, TopicStatus
from app.domain.usage.ledger import (
    Payer,
    payer_for_person,
    payer_for_project,
    payers_for_projects,
)
from app.domain.user.models import User
from app.domain.user.services import user_by_handle


class SandboxHolder(Protocol):
    """Whose sandboxes these are, and what follows from it."""

    @property
    def id(self) -> uuid.UUID:
        """The conversation a sandbox's row names (``SandboxHome.topic_id``)."""
        ...

    @property
    def namespace(self) -> uuid.UUID:
        """The directory the holder's sandboxes, and their package store, sit
        under on a machine."""
        ...

    @property
    def project_id(self) -> uuid.UUID | None:
        """The project the holder's work is in, if any: what a machine of
        somebody's has to serve to take that work (``environments``)."""
        ...

    @property
    def generation(self) -> str:
        """The holder's resource now: a sandbox made for an earlier one is not
        this holder's any more (``SandboxHome.room_resource_id``), and a
        session's credential names the one it was signed for (``r``)."""
        ...

    def home(self) -> dict:
        """The columns a new sandbox's row takes from its holder."""
        ...

    def signed_for(self, claims: dict) -> bool:
        """Whether a session's credential was signed for this holder as it is."""
        ...

    def credential(self, token: str, *, session_id: str, lease_generation: str) -> str:
        """The credential a session's calls into its sandbox carry
        (``routes/execution.py``), from the one it asked for the sandbox with."""
        ...

    def sandbox_token(self, credential: str) -> str:
        """What the sandbox's executor itself is given to call the platform
        with, out of the session's ``credential``."""
        ...

    async def require_use(self, db: AsyncSession, actor: Actor) -> None:
        """Raise unless ``actor`` may have a sandbox started for this holder."""
        ...

    async def payer(self, db: AsyncSession) -> Payer:
        """Who pays for this holder's sandboxes."""
        ...

    async def environment(
        self, db: AsyncSession, session: AgentSession
    ) -> Sandbox | SelfHosted:
        """Where ``session``, working for this holder, does its work."""
        ...


@dataclass(frozen=True, slots=True)
class Room:
    """A channel: its sessions' sandboxes are the room's, on its project."""

    id: uuid.UUID
    project_id: uuid.UUID
    generation: str

    @classmethod
    def of(cls, topic: Topic) -> "Room":
        return cls(
            id=topic.id,
            project_id=topic.project_id,
            generation=str(topic.resource_id or topic.id),
        )

    @property
    def namespace(self) -> uuid.UUID:
        # Its sessions' sandboxes share the project's package store.
        return self.project_id

    def home(self) -> dict:
        return {
            "project_id": self.project_id,
            "topic_id": self.id,
            "room_resource_id": self.generation,
        }

    def signed_for(self, claims: dict) -> bool:
        return claims.get("p") == str(self.project_id) and (
            claims.get("r") == self.generation
        )

    def credential(self, token: str, *, session_id: str, lease_generation: str) -> str:
        return bind_resource_token(
            token,
            self.generation,
            session_id=session_id,
            lease_generation=lease_generation,
        )

    def sandbox_token(self, credential: str) -> str:
        # The executor's own `cheese` acts in the room: syncing a task's
        # checkout, the preview tunnel.
        return credential

    async def require_use(self, db: AsyncSession, actor: Actor) -> None:
        await require_project_member(db, self.project_id, actor)

    async def payer(self, db: AsyncSession) -> Payer:
        return await payer_for_project(db, self.project_id)

    async def environment(
        self, db: AsyncSession, session: AgentSession
    ) -> Sandbox | SelfHosted:
        # The session's own choice, else its task's, its room's or its
        # project's default (``environments``).
        topic = await db.get(Topic, self.id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        project = await ProjectService(db).get_or_404(self.project_id)
        return await environment_of(
            db, session, topic, await task_of(db, self, session), project
        )


@dataclass(frozen=True, slots=True)
class Personal:
    """One topic of a person's thread with their own 芝士: its sandbox is in
    no project, and is that person's alone."""

    id: uuid.UUID
    user_id: int
    handle: str

    @property
    def namespace(self) -> uuid.UUID:
        # Nothing of it is shared, the package store included.
        return self.id

    @property
    def project_id(self) -> None:
        return None

    @property
    def generation(self) -> str:
        return str(self.id)

    def home(self) -> dict:
        return {
            "project_id": None,
            "topic_id": self.id,
            "room_resource_id": self.generation,
        }

    def signed_for(self, claims: dict) -> bool:
        return claims.get("u") == self.user_id and claims.get("r") == self.generation

    def credential(self, token: str, *, session_id: str, lease_generation: str) -> str:
        return mint_personal_execution_credential(
            user_id=self.user_id,
            conversation_id=str(self.id),
            session_id=session_id,
            lease_generation=lease_generation,
        )

    def sandbox_token(self, credential: str) -> str:
        # The sandbox holds no credential at all: everything its 芝士 does on
        # the platform it does from outside, with the question's own.
        return ""

    async def require_use(self, db: AsyncSession, actor: Actor) -> None:
        if actor.handle != self.handle:
            raise ForbiddenError(say("cloudMembersOnly"))

    async def payer(self, db: AsyncSession) -> Payer:
        return await payer_for_person(db, self.user_id)

    async def environment(
        self, db: AsyncSession, session: AgentSession
    ) -> Sandbox | SelfHosted:
        # A standard sandbox on the platform's machines, always: nobody's own
        # computer serves a person's 芝士, and a whole VM is a project's.
        return Sandbox(spec=STANDARD)


async def _personal(
    db: AsyncSession, holder_id: uuid.UUID, *, locked: bool = False
) -> Personal | None:
    query = (
        select(AssistantConversation.id, AssistantConversation.user_id, User.username)
        .join(User, User.id == AssistantConversation.user_id)
        .where(AssistantConversation.id == holder_id)
    )
    if locked:
        # The topic's row, as a room's lock is its row: an answer being written
        # into it takes only a key share, which this leaves free.
        query = query.with_for_update(key_share=True, of=AssistantConversation)
    found = (await db.execute(query)).one_or_none()
    if found is None:
        return None
    holder_id, user_id, handle = found
    return Personal(id=holder_id, user_id=user_id, handle=handle)


#: The channel a person's 芝士 session is recorded under: none of a room's
#: channels reads it back (``central_provider.placed``).
PERSONAL_CHANNEL = "personal"


async def personal_claims(
    db: AsyncSession, token: str, conversation_id: uuid.UUID, session_id: uuid.UUID
) -> dict | None:
    """What a person's 芝士 session asks for its sandbox with, said as a room
    session's credential says it (``session_work.ensure``): the person's own
    credential for this conversation, which must still be theirs. None when
    ``token`` is no such credential."""
    said = personal_claims_of(token)
    if said is None or said.conversation_id != str(conversation_id):
        return None
    personal = await _personal(db, conversation_id)
    if personal is None or personal.user_id != said.user_id:
        return None
    # A topic has one session, named by the topic itself, which runs on the
    # deployment's session host (``agent.personal.session``).
    if session_id != conversation_id:
        return None
    await db.execute(
        insert(AgentSession)
        .values(
            id=conversation_id,
            conversation_id=conversation_id,
            agent_handle=personal.handle,
            harness=PI,
            runtime_location={
                "device_id": settings.agent_session_device_id,
                "channel": PERSONAL_CHANNEL,
                "resource_id": personal.generation,
                "session_id": str(conversation_id),
            },
        )
        .on_conflict_do_nothing(index_elements=[AgentSession.id])
    )
    return {
        "u": personal.user_id,
        "a": personal.handle,
        "t": str(conversation_id),
        "r": personal.generation,
        "session": str(session_id),
    }


async def _kind(
    db: AsyncSession, conversation_id: uuid.UUID
) -> ConversationKind | None:
    # A session's every tool call asks it twice, once for its holder and once
    # for its own conversation, which are the same one in a room.
    can_cache = isinstance(db, AsyncSession)
    if can_cache:
        if db.autoflush:
            await db.flush()
        found, kind = recall(db, ("conversation_kind", conversation_id))
        if found:
            return kind
    kind = await db.scalar(
        select(Conversation.kind).where(Conversation.id == conversation_id)
    )
    if can_cache:
        remember(
            db,
            ("conversation_kind", conversation_id),
            kind,
            tables=frozenset({"conversations"}),
        )
    return kind


async def task_of(db: AsyncSession, room: Room, session: AgentSession) -> Task | None:
    """The task ``session`` works in, when it works in one of the room's tasks
    rather than in the room itself."""
    if session.conversation_id == room.id:
        return None
    return await db.get(Task, session.conversation_id)


async def require_project_member(
    db: AsyncSession, project_id: uuid.UUID, actor: Actor
) -> None:
    """Being on the project authorizes running its sessions on cloud.

    The project's roster is the whole question: its owner, its team's members
    (agents seated on the team included) and its external members. What is
    still required is a signed-in identity to look up on that roster.
    """
    user_id = actor.user_id
    if actor.via == "cheese":
        user = await user_by_handle(db, actor.handle)
        user_id = user.id if user else None
    elif actor.via != "token":
        raise AuthenticationRequiredError()
    if user_id is None:
        raise AuthenticationRequiredError()
    project = await ProjectService(db).get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))

    if not any(
        m.handle == actor.handle
        for m in await roster(db, project_id, team=project_team)
    ):
        raise ForbiddenError(say("cloudMembersOnly"))


async def holder_of(db: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID:
    """Which holder the sandboxes a session in this conversation works in
    belong to: a task's or a 支线's are its room's, and a personal one holds
    its own."""
    if await _kind(db, conversation_id) == ConversationKind.personal:
        return conversation_id
    return await room_of(db, conversation_id)


async def look(db: AsyncSession, holder_id: uuid.UUID) -> SandboxHolder | None:
    """The holder as it is, read without a lock; None when it is gone or takes
    no tool call (an archived room)."""
    # deferred-import: tests replace this name on app.domain.topic.services
    from app.domain.topic.services import TopicService

    if await _kind(db, holder_id) == ConversationKind.personal:
        return await _personal(db, holder_id)
    topic = await TopicService(db).get(holder_id)
    if topic is None or topic.status == TopicStatus.archived:
        return None
    return Room.of(topic)


async def lock_for_work(db: AsyncSession, holder_id: uuid.UUID) -> SandboxHolder:
    """Lock the holder for a tool call to be admitted under, and answer it as
    it is now. Raises when it takes no tool call (an archived room)."""
    # deferred-import: tests replace this name on app.domain.topic.services
    from app.domain.topic.services import TopicService

    if await _kind(db, holder_id) == ConversationKind.personal:
        personal = await _personal(db, holder_id, locked=True)
        if personal is None:
            raise NotFoundError.for_resource("conversation", str(holder_id))
        return personal
    # The lock reads the row afresh, so an archival that committed while this
    # waited is seen.
    return Room.of(await TopicService(db).lock_for_execution(holder_id))


async def lock(db: AsyncSession, holder_id: uuid.UUID) -> None:
    """Lock the holder whatever state it is in: for work that orders itself
    against the holder's own, such as destroying an archived room's sandbox."""
    # deferred-import: tests replace this name on app.domain.topic.services
    from app.domain.topic.services import TopicService

    if await _kind(db, holder_id) == ConversationKind.personal:
        await _personal(db, holder_id, locked=True)
        return
    await TopicService(db).lock(holder_id)


async def payers(
    db: AsyncSession, holder_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Payer]:
    """Each holder's payer, read together. Read off the register of
    conversations, which still names a room that has since become a task in
    place (``SandboxHome.topic_id`` keeps its id)."""
    rows = await db.execute(
        select(Conversation.id, Conversation.project_id).where(
            Conversation.id.in_(set(holder_ids))
        )
    )
    held: dict[uuid.UUID, uuid.UUID] = {}
    personal: list[uuid.UUID] = []
    for holder_id, project_id in rows.all():
        if project_id is None:
            personal.append(holder_id)
        else:
            held[holder_id] = project_id
    projects = await ProjectService(db).get_projects_by_ids(list(set(held.values())))
    by_project = await payers_for_projects(db, list(projects.values()))
    out = {
        holder_id: by_project[project_id]
        for holder_id, project_id in held.items()
        if project_id in by_project
    }
    owners = (
        dict(
            (
                await db.execute(
                    select(
                        AssistantConversation.id, AssistantConversation.user_id
                    ).where(AssistantConversation.id.in_(personal))
                )
            )
            .tuples()
            .all()
        )
        if personal
        else {}
    )
    by_person: dict[int, Payer] = {}
    for holder_id, user_id in owners.items():
        if user_id not in by_person:
            by_person[user_id] = await payer_for_person(db, user_id)
        out[holder_id] = by_person[user_id]
    return out


async def payer_of(db: AsyncSession, holder_id: uuid.UUID) -> Payer | None:
    """One holder's payer; None when it is gone."""
    return (await payers(db, [holder_id])).get(holder_id)


async def at_work(db: AsyncSession, holder_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Those of the holders running a turn now: a room is, while any
    conversation in it is. A person's topic runs no turns: an answer is not
    held against its sandbox once the credits are spent."""
    if not holder_ids:
        return set()
    return set(
        await db.scalars(
            select(room_column(AgentTurn.conversation_id))
            .where(
                of_rooms(AgentTurn.conversation_id, holder_ids),
                AgentTurn.stopped_at.is_(None),
            )
            .distinct()
        )
    )


def namespace_of(home: SandboxHome | Row) -> uuid.UUID:
    """The directory a sandbox's home sits under on its machine, read off its
    row (a ``SandboxHome``, or a row of its columns): its holder's
    ``namespace`` when it was made — its project, else its own conversation."""
    return home.project_id or home.topic_id
