"""A member's own Claude Code, run on their own machine (#2991).

The session works exactly as a central one does — the remote-execution client,
the executor on the leased machine, its sandbox, the work lease, the platform
tools — with both ends on the owner's machine: the process that talks to the
model runs there, with the login its owner gave the platform
(`cheesehost claude login`), and the hands are that same machine. Nothing runs
on the platform's session host and nothing goes through the metering proxy.

Which machine: one of the owner's, online, with that login. The one the
session already ran on comes first, so a conversation stays where its
transcript is while that machine is there.
"""

import logging
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.core.sentences import say
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent_instance.own import owned_by_session, owned_instance
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow, HostedDeviceRow
from app.domain.device.supply import Supply
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
        place = await AgentSessionService(db).place(
            session.topic_id, session.agent_handle, harness=session.harness
        )
        machines = await owners_machines(db, owned.owner_user_id)
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

    def _deferred_target(self, target: dict) -> dict:
        # The hands are this same machine: the client sees the project at the
        # executor's own path, with no forwarded view and no namespace.
        return {**target, "local": True}

    @asynccontextmanager
    async def prepare_session(self, **kwargs):
        async with super().prepare_session(**kwargs) as prepared:
            # Read by the screen (`DeviceChannel._ensure_screen`) and the
            # launch: this session signs in with its owner's login on the
            # machine, and none of the metering proxy's environment is set.
            prepared.env["CHEESE_OWN_LOGIN"] = "1"
            yield prepared


async def owners_machines(db, owner_user_id: int) -> list[str]:
    """The owner's own machines with the platform's login, most recently
    confirmed first."""
    rows = await db.execute(
        select(DeviceRow.device_id)
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
    return [device_id for (device_id,) in rows]


async def owner_is_away(db, instance_id, hub) -> bool:
    """Whether ``instance_id`` is a member's own agent none of whose owner's
    machines is online with its login: its turn has nowhere to run until one
    comes back."""
    owned = await owned_instance(db, instance_id)
    if owned is None:
        return False
    machines = await owners_machines(db, owned.owner_user_id)
    return not any(hub.is_online(device) for device in machines)
