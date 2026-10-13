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

Which machine runs the session: one of the owner's, online, with that login,
of a kind the project allows (`own.allowed_kinds`). The one the session already
ran on comes first, so a conversation stays where its transcript is while that
machine is there. When the project allows none of the owner's logins the
session does not start, and the room is told why.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.sentences import say
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.own import (
    allowed_kinds,
    kinds_refused,
    kinds_refused_sentence,
    owned_by_session,
    owned_instance,
    owner_of,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.login_kind import ConnectionKind, connection_kind
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow, HostedDeviceRow
from app.domain.device.supply import Supply
from app.domain.project.reads import load_project
from app.domain.topic.services import TopicService

logger = logging.getLogger(__name__)

#: What a compute choice and a session's placement name this channel by.
OWNER_CHANNEL = "owner"


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
        settings = project.settings if project is not None else None
        refused = await kinds_refused(db, owned.owner_user_id, settings)
        if refused:
            owner = await owner_of(db, owned.instance_id)
            raise ScreenSetupError(
                kinds_refused_sentence(owner[1] if owner else "", refused)
            )
        place = await AgentSessionService(db).place(
            session.topic_id, session.agent_handle, harness=session.harness
        )
        machines = await owners_machines(
            db, owned.owner_user_id, allowed_kinds(settings)
        )
        online = [device for device in machines if self._hub.is_online(device)]
        if place is not None and place.machine in online:
            return place.machine
        if online:
            return online[0]
        raise ScreenSetupError(say("ownerMachineOffline"))

    def _center(self, place, precheck: Placement) -> str | None:
        # The owner's machine this turn resolved, not a recorded one that may
        # have gone: a conversation moves with its owner between machines.
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


async def owners_machines(
    db, owner_user_id: int, kinds: frozenset[ConnectionKind]
) -> list[str]:
    """The owner's own machines with the platform's login of one of ``kinds``,
    most recently confirmed first."""
    rows = await db.execute(
        select(
            DeviceRow.device_id,
            DeviceClaudeLoginRow.auth_method,
            DeviceClaudeLoginRow.subscription_type,
        )
        .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
        .join(
            DeviceClaudeLoginRow,
            DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
        )
        .where(
            DeviceRow.owner_user_id == owner_user_id,
            DeviceRow.supply == Supply.self_hosted,
            DeviceClaudeLoginRow.logged_in.is_(True),
        )
        .order_by(DeviceClaudeLoginRow.checked_at.desc())
    )
    return [
        device_id
        for device_id, auth_method, subscription_type in rows
        if connection_kind(auth_method, subscription_type) in kinds
    ]


async def owner_is_away(db, instance_id, hub) -> bool:
    """Whether ``instance_id`` is a member's own agent none of whose owner's
    machines is online with a login the project allows: its turn has nowhere
    to run until one comes back. Not when the project allows none of the
    owner's logins: that turn starts, to be refused with the reason."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return False
    instance = await db.get(AgentInstance, instance_id)
    project = await load_project(db, instance.project_id) if instance else None
    settings = project.settings if project is not None else None
    if await kinds_refused(db, owned.owner_user_id, settings):
        return False
    machines = await owners_machines(db, owned.owner_user_id, allowed_kinds(settings))
    return not any(hub.is_online(device) for device in machines)
