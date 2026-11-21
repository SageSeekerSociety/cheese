"""Whom a sandbox's request is answered as.

A sandbox holds no credential: the process that owns the device links names
the device and the state directory its request came in on
(``device_hub.machine``), which is the environment the request came from.
What a request from an environment may do is the environment's authority;
whose name it carries is the attachment that started the command, checked to
be attached there now. A request may name one (``X-Cheese-Attachment``, from
the environment of the command that sent it): an agent session's is answered
as that session, a person's on the environment's authority in that person's
name, and a name that is not attached to that environment right now is
nobody. A request that names nobody is the environment itself as the
principal, in the conversation that points to it. Authority only a person has
never enters an environment: neither a person's attachment nor the
environment itself is ever answered with a person's own roles
(`credential_of`). It is looked up on every request, so an attachment that
ended, moved or was made anew is nobody from its next request on.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, for_a_machine, mint_scoped_token
from app.domain.agent.place import ROOT_PLACEHOLDER, footprint_root
from app.domain.agent_instance.adapters import project_team
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.services import room_of
from app.domain.environment import services as environment_rows
from app.domain.environment.models import (
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.machine import holder as holders
from app.domain.machine.session_work import environment_credential, session_author
from app.domain.membership.roster import roster
from app.domain.project.services import ProjectService
from app.domain.room_task.place import session_keeps_work
from app.domain.topic.services import TopicService
from app.domain.user.models import User

#: How long the credential a socket request is served under lives: it is made
#: for that one request and never leaves the backend.
CALL_CREDENTIAL_TTL_S = 300
#: How long the credential a sandbox is handed for what cannot go down its
#: socket (git's own requests, the preview tunnel, a body over the socket's
#: limit) lives: it is asked for right before that use.
HANDED_CREDENTIAL_TTL_S = 60


#: Who a request is answered as (`MachineCaller.kind`).
AGENT = "agent"
PERSON = "person"
ENVIRONMENT = "environment"


@dataclass(frozen=True)
class MachineCaller:
    """Whom a sandbox's request is answered as: an agent session attached to
    the environment, a person attached to it, or the environment itself."""

    #: ``AGENT``, ``PERSON`` or ``ENVIRONMENT``.
    kind: str
    project_id: str
    #: The conversation the request acts in: the attacher's, or for the
    #: environment itself the one that points to it.
    conversation_id: str
    environment_id: str
    #: Whose name the request carries: the seat an agent acts under, as its
    #: own launch credential names it (`session_work.session_author`), or a
    #: person's handle. None for the environment itself.
    handle: str | None = None
    #: The agent's session; None for anybody else.
    session_id: str | None = None
    #: The attachment's generation: what a credential minted for it names.
    generation: str | None = None
    #: The session row as the lookup read it, on the request's own database
    #: session. Held here so the credential made for this request
    #: (`credential_of`) reads it from that session's identity map, which keeps
    #: a row only while something refers to it, instead of reading it again.
    row: AgentSession | None = field(default=None, compare=False, repr=False)
    #: The environment, read with ``row``.
    environment: dict | None = field(default=None, compare=False, repr=False)

    @property
    def agent_handle(self) -> str | None:
        """The seat an agent acts under; None for a person or the
        environment, which hold no seat (a model call needs one)."""
        return self.handle if self.kind == AGENT else None


def machine_proxy_proof(project_id: str) -> str:
    """The proof a host-side proxy outside the sandbox presents on its
    platform socket (`X-Cheese-Proxy-Proof`): derived from the platform's
    signing secret and the project id, handed to `bootstrap.configure_idle`
    outside the sandbox and stripped before the sandbox starts."""
    return hmac.new(
        settings.sandbox_signing_secret.encode(),
        f"machine-proxy:{project_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def valid_proxy_proof(project_id: str, proof: str) -> bool:
    return bool(proof) and hmac.compare_digest(proof, machine_proxy_proof(project_id))


def _names(state: str, tag: str) -> bool:
    # The connector is told a state as the backend spelled it for that
    # connector (`HubDevice.spelled`): the root placeholder expanded for one
    # that predates enrollment roots.
    return tag in (state, state.replace(ROOT_PLACEHOLDER, footprint_root()))


async def caller_of(
    db: AsyncSession, device: str, state: str, attachment: str | None = None
) -> MachineCaller | None:
    """Who a request from ``state`` on ``device`` is answered as: the
    attachment ``attachment`` names — an agent session's or a person's — or,
    naming nobody, the environment itself. None when the name is not
    attached there now, the environment does not answer, or it is in no
    project."""
    named: uuid.UUID | None = None
    if attachment:
        try:
            named = uuid.UUID(attachment)
        except ValueError:
            return None
    rows = await db.execute(
        select(
            Environment,
            environment_rows.described(
                environment_rows.key("generation"),
                EnvironmentAttachment.generation,
            ),
            EnvironmentAttachment,
            AgentSession,
        )
        .outerjoin(
            EnvironmentAttachment,
            and_(
                EnvironmentAttachment.environment_id == Environment.id,
                EnvironmentAttachment.id == named,
            ),
        )
        .outerjoin(AgentSession, AgentSession.id == EnvironmentAttachment.session_id)
        .where(Environment.device_id == device)
    )
    held = next(
        (
            found
            for found in rows
            if found[1].get("status", "ready") == "ready"
            and found[1].get("state")
            and _names(found[1]["state"], state)
        ),
        None,
    )
    if held is None:
        return None
    environment, described, attached, session = held
    if named is not None and attached is None:
        return None
    project = (
        await ProjectService(db).get(environment.project_id)
        if environment.project_id is not None
        else None
    )
    if project is None:
        return None
    common = {
        "project_id": str(project.id),
        "environment_id": str(environment.id),
        "environment": described,
    }
    if attached is None:
        pointing = await db.scalar(
            select(ConversationEnvironment.conversation_id)
            .where(ConversationEnvironment.environment_id == environment.id)
            .limit(1)
        )
        return MachineCaller(
            kind=ENVIRONMENT,
            conversation_id=str(pointing or environment.topic_id),
            **common,
        )
    if session is not None:
        return MachineCaller(
            kind=AGENT,
            conversation_id=str(session.conversation_id),
            handle=await session_author(db, project, session.agent_handle),
            session_id=str(session.id),
            generation=attached.generation,
            row=session,
            **common,
        )
    person = await db.get(User, attached.user_id)
    if (
        person is None
        or person.deleted_at is not None
        or not any(
            member.handle == person.username
            for member in await roster(db, project.id, team=project_team)
        )
    ):
        # No longer on the project: the attachment ends with its next use.
        await db.execute(
            delete(EnvironmentAttachment).where(EnvironmentAttachment.id == attached.id)
        )
        await db.commit()
        return None
    return MachineCaller(
        kind=PERSON,
        conversation_id=str(attached.conversation_id),
        handle=person.username,
        generation=attached.generation,
        **common,
    )


async def credential_of(
    db: AsyncSession,
    caller: MachineCaller,
    *,
    ttl_s: int = CALL_CREDENTIAL_TTL_S,
    held: bool = True,
) -> str | None:
    """The credential the caller's request is served under, made again for
    one request, and marked as held by a machine (`held=True`) unless kept
    in-process for `/machine/api` (`held=False`). An agent's is its
    session's launch credential bound to its attachment (as
    `session_work.restart_executor` makes it), a 支线's marked as keeping no
    work. A person's or the environment's own is the environment's authority
    (`session_work.environment_credential`): it names no agent and never
    resolves to a person's own roles. None when the session is gone."""
    if caller.kind != AGENT:
        holder = await holders.look(
            db, await holders.holder_of(db, uuid.UUID(caller.conversation_id))
        )
        if holder is None:
            return None
        made = await environment_credential(
            db,
            holder,
            conversation_id=uuid.UUID(caller.conversation_id),
            environment_id=uuid.UUID(caller.environment_id),
            lease_generation=caller.generation,
            by=caller.handle,
            ttl_s=ttl_s,
        )
        return for_a_machine(made) if held else made
    assert caller.session_id is not None and caller.handle is not None
    row = caller.row or await db.get(AgentSession, uuid.UUID(caller.session_id))
    if row is None:
        return None
    lease = caller.environment or {}
    # A ready environment names its room's resource; only one that does not
    # is given the room's own.
    resource = lease.get("room_resource_id")
    if not resource:
        room = await TopicService(db).get_or_404(await room_of(db, row.conversation_id))
        resource = str(room.resource_id or room.id)
    launch = mint_scoped_token(
        project_id=caller.project_id,
        topic_id=caller.conversation_id,
        agent_handle=caller.handle,
        access_scope="project",
        ttl_s=ttl_s,
    )
    bound = bind_resource_token(
        launch,
        resource,
        session_id=caller.session_id,
        lease_generation=caller.generation,
        scratch=not await session_keeps_work(db, row.id),
    )
    return for_a_machine(bound) if held else bound
