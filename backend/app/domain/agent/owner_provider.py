"""A member's own Claude Code, run on their own machine (#2991).

Only the process that talks to the model runs there, with the login its owner
gave the platform (`cheesehost claude login`): a subscription's login stays on
the machine it was given on. Its tools go to its execution environment like
any session's (#2320) — the remote-execution client, the executor in that
environment, its sandbox, the work lease and the platform tools are a central
session's. Nothing runs on the platform's session host and nothing goes
through the metering proxy.

The owner's machine has no forwarded project view and no namespace of its own
(both are Linux's, and the session host here may be a Mac or a Windows PC).
So when the environment is on that same machine the session sees the project
where the executor holds it; when it is elsewhere, the session works in a
directory of its own holding a copy of the project's instructions, skills,
rules and agents, and its paths are rewritten to the executor's on their way
there (`client.prepare`).

Which machine runs the session: the one holding the model connection the
agent was added with, and no other — a subscription's login never leaves the
machine it was given on. While that machine is away the session waits for it.
When the connection is gone (its owner removed or logged it out), or its kind
or its model service's host is one the project does not allow, the session
does not start, and the room is told why (`own.unusable`).

A connection its owner's account holds instead — an API key or a model
service, kept sealed by the platform — is not tied to any machine, so its
session runs on the deployment's session host like the project's own agents
(`AccountChannel`), calling the model with that key.
"""

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager

from app.core.db import async_session_factory
from app.core.errors import BaseError
from app.core.sentences import say
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.place import (
    OWN_KEY,
    OWN_MODEL,
    OWN_MODEL_AUTH,
    OWN_MODEL_BASE_URL,
    OWN_MODEL_KEY,
)
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.own import (
    owned_by_session,
    owned_instance,
    unusable,
)
from app.domain.device.login_kind import ConnectionKind
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.project.reads import load_project
from app.domain.topic.services import TopicService

logger = logging.getLogger(__name__)

#: What a compute choice and a session's placement name this channel by.
OWNER_CHANNEL = "owner"
#: And the channel of one on a connection its owner's account holds.
ACCOUNT_CHANNEL = "own_account"


class OwnerChannel(CentralChannel):
    def __init__(self, executor):
        super().__init__(executor)
        self.name = OWNER_CHANNEL

    def available(self):
        # Whether a session can run is a question about one owner's machines,
        # answered per session in `precheck`.
        return True

    async def _resolve_session_host(self, db, session: SessionRef) -> str:
        topic = await TopicService(db).get_or_404(session.topic_id)
        owned = await owned_by_session(db, topic.project_id, session.agent_handle)
        if owned is None:
            raise ScreenSetupError(say("screenAgentIdentityMissing"))
        project = await load_project(db, topic.project_id)
        reason = await unusable(
            db, owned, project.settings if project is not None else None
        )
        if reason is not None:
            raise ScreenSetupError(reason)
        if owned.device_id is not None and self._hub.is_online(owned.device_id):
            return owned.device_id
        raise ScreenSetupError(say("ownerMachineOffline"))

    def _center(self, place, precheck: Placement) -> str | None:
        # The machine holding its connection, as this turn resolved it.
        return precheck.machine

    def _deferred_target(self, target: dict, *, apart: bool) -> dict:
        # No forwarded view and no namespace on the owner's machine: the
        # session sees the project at the executor's own path when the
        # executor is on this machine, and in a directory of its own when it
        # is on another (`apart`) or not yet leased.
        return {**target, "local": True, **({"apart": True} if apart else {})}

    @asynccontextmanager
    async def prepare_session(self, **kwargs):
        async with super().prepare_session(**kwargs) as prepared:
            # Read by the screen (`DeviceChannel._ensure_screen`) and the
            # launch: this session signs in with its owner's login on the
            # machine, and none of the metering proxy's environment is set.
            prepared.env["CHEESE_OWN_LOGIN"] = "1"
            # What the machine calls a model with is read once, as the session
            # starts. Folded into what the session was started with, so the
            # next turn after its owner switches account or model service
            # starts a new session instead of keeping the old one.
            calls = await self._model_access(kwargs["precheck"].machine)
            prepared.env["CHEESE_AGENT_CONFIG"] = hashlib.sha256(
                f"{prepared.env.get('CHEESE_AGENT_CONFIG', '')}:{calls}".encode()
            ).hexdigest()
            yield prepared

    async def _model_access(self, machine: str) -> str:
        """How the owner's Claude Code on ``machine`` reaches a model, as it
        last said: the kind of login, and a model service's model."""
        async with (self._session_factory or async_session_factory)() as db:
            row = await db.get(DeviceClaudeLoginRow, machine)
        if row is None:
            return ""
        return f"{row.auth_method or ''}:{row.model or ''}"


class AccountChannel(CentralChannel):
    """A member's own Claude Code on a connection its owner's account holds:
    an Anthropic API key or a model service (`AccountConnection`).

    It runs where the project's own agents run — the deployment's session host,
    its tools in the execution environment its conversation has — and calls
    the model with that connection, directly: none of the metering proxy
    reaches it (no placeholder login, no CA, no tunnel), so neither the
    platform's credential nor its gateway is used, and its owner's key pays.
    The key is opened here, as the session starts, and handed to that one
    process's environment; the execution environment is never given it.

    When the connection is gone, or of a kind or at a model-service host the
    project does not allow, the session does not start and the room is told why
    (`own.unusable`).
    """

    def __init__(self, executor):
        super().__init__(executor)
        self.name = ACCOUNT_CHANNEL

    async def _resolve_session_host(self, db, session: SessionRef) -> str:
        await self._connection(db, session)
        return await super()._resolve_session_host(db, session)

    async def _connection(self, db, session: SessionRef):
        topic = await TopicService(db).get_or_404(session.topic_id)
        owned = await owned_by_session(db, topic.project_id, session.agent_handle)
        if owned is None:
            raise ScreenSetupError(say("screenAgentIdentityMissing"))
        project = await load_project(db, topic.project_id)
        # Removed by its owner, its row names no connection any more.
        reason = await unusable(
            db, owned, project.settings if project is not None else None
        )
        if reason is not None:
            raise ScreenSetupError(reason)
        row = (
            await account_connections.owned(
                db, owned.owner_user_id, owned.account_connection_id
            )
            if owned.account_connection_id is not None
            else None
        )
        if row is None:
            raise ScreenSetupError(say("screenAgentIdentityMissing"))
        return row

    @asynccontextmanager
    async def prepare_session(self, **kwargs):
        async with (self._session_factory or async_session_factory)() as db:
            row = await self._connection(db, kwargs["session"])
            base_url = account_connections.base_url_of(row)
            key = account_connections.key_of(row)
            kind = ConnectionKind(row.kind)
            model = row.model
            identity = f"{row.id}:{base_url}:{model or ''}"
        # Its address was checked as its owner saved it; a name can be
        # pointed elsewhere since, and this host calls it next.
        try:
            await asyncio.to_thread(account_connections.refuse_unsafe_address, base_url)
        except BaseError as refused:
            raise ScreenSetupError(refused.message) from refused
        model_env = {
            OWN_KEY: "1",
            OWN_MODEL_BASE_URL: base_url,
            OWN_MODEL_KEY: key,
            OWN_MODEL_AUTH: "api_key" if kind is ConnectionKind.api_key else "bearer",
            **({OWN_MODEL: model} if model else {}),
        }
        async with super().prepare_session(**kwargs) as prepared:
            # Added only now, after the execution target was built and
            # recorded: what the execution environment is handed (its
            # `setup_env` copies the session's own variables) never holds it.
            prepared.env.update(model_env)
            # Which connection the session calls is read once, as it starts:
            # folded into what it was started with, so the next turn after its
            # owner removes and re-adds a service starts a new one.
            prepared.env["CHEESE_AGENT_CONFIG"] = hashlib.sha256(
                f"{prepared.env.get('CHEESE_AGENT_CONFIG', '')}:{identity}".encode()
            ).hexdigest()
            yield prepared


async def owner_is_away(db, instance_id, hub) -> bool:
    """Whether ``instance_id`` is a member's own agent whose connection's
    machine is not online: its turn has nowhere to run until it comes back.
    Not when the connection is gone or one the project refuses: that turn
    starts, to be refused with the reason. Never one on a connection its
    owner's account holds, which runs on the session host."""
    owned = await owned_instance(db, instance_id)
    if owned is None or owned.on_account:
        return False
    instance = await db.get(AgentInstance, instance_id)
    project = await load_project(db, instance.project_id) if instance else None
    if await unusable(db, owned, project.settings if project is not None else None):
        return False
    return not hub.is_online(owned.device_id)
