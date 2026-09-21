"""The concrete MicroCloud channel for one-machine-per-topic Cloud."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.device_hub import DeviceHub, device_hub
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService


@dataclass(frozen=True, slots=True)
class CloudLease:
    project_id: uuid.UUID
    device_id: str | None
    machine_ready: bool
    ai_ready: bool
    error: str | None = None


EnsureTopicCloud = Callable[[uuid.UUID, Actor | None], Awaitable[CloudLease]]
ReadTopicCloud = Callable[[uuid.UUID], Awaitable[CloudLease | None]]


class CloudChannel(DeviceChannel):
    """One-room lease resolution over the existing device transport.

    Inheritance here is not the M×N the composition split removed: a leased
    Cloud machine IS a device, reached over the same link with the same screen —
    only WHICH device is different. What used to be re-inherited per transport
    was the harness, and that now lives above the seam for both of these.

    Everything overridden below answers one question — WHICH machine, and is it
    up yet. None of it touches the model environment: ``builds_model_env`` is
    inherited because ``_ensure_screen`` is, so a leased machine takes the same
    launch shape an enrolled one takes — and that shape names no model at all,
    because which model a turn runs on is resolved at admission. Code that asks
    which of the two a turn is on in order to answer THAT is asking the wrong
    question.

    The machine is the ROOM's, and a room is the only thing that runs a turn:
    work inside a room is a 分身 in that room's own session, on that room's
    machine.
    """

    name = "cloud"
    # 平台开的机器。同一台物理 VM 由人自己接进来时是 self_hosted：入口决定待遇，
    # 机器长什么样不决定 (#282 决定 2)。基类的 `owns` 读的就是这一位。
    supply = Supply.cloud
    provisions_machine = True
    # 要手要不到的那一句。要不要手、要不到就停，那条分支在基类上只有一份 —— 这
    # 条通道改的只有供给和这一句话。
    no_machine_message = "Cloud 机器尚未完成连接"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker | None = None,
        hub: DeviceHub | None = None,
        configured: bool,
        ensure_topic_cloud: EnsureTopicCloud,
        read_topic_cloud: ReadTopicCloud,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            hub=hub,
        )
        self._hub = hub or device_hub
        self._configured = configured
        self._ensure_topic_cloud = ensure_topic_cloud
        self._read_topic_cloud = read_topic_cloud

    def available(self) -> bool:
        return self._configured

    async def prepare_topic(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        actor: Actor | None = None,
    ) -> tuple[bool, str]:
        """Provision/poll before ChatService counts a prompt delivery attempt."""
        lease = await self._ensure_topic_cloud(topic_id, actor)
        if lease.project_id != project_id:
            raise ScreenSetupError("topic cloud machine belongs to another project")
        if lease.error:
            raise ScreenSetupError(lease.error)
        ready = (
            lease.machine_ready
            and lease.ai_ready
            and lease.device_id is not None
            and self._hub.is_online(lease.device_id)
        )
        if ready:
            return True, ""
        return False, "Cloud 机器正在创建并接入"

    async def _resolve_device_agent(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str] | None:
        lease = await self._read_topic_cloud(topic_id)
        if lease is None or lease.project_id != project_id:
            raise ScreenSetupError("本话题没有自己的 Cloud 机器")
        if lease.device_id is None or not self._hub.is_online(lease.device_id):
            return None
        async with self._sessions() as session:
            devices = sql_device_service(session)
            endpoint = await devices.get_device(lease.device_id)
            if endpoint is None or endpoint.supply is not Supply.cloud:
                raise ScreenSetupError("本话题的 Cloud 机器没有有效的云端连接器")
            binding = await devices.topic_binding(topic_id)
            if binding is not None and binding.device_id != lease.device_id:
                raise ScreenSetupError(
                    "Cloud 话题已绑定到别的端点；拒绝借用另一话题的机器"
                )
            if binding is None:
                await devices.bind_topic_device(
                    topic_id,
                    lease.device_id,
                    visibility=await devices.binding_visibility(lease.device_id),
                )
            # 这个房间的 agent 身份：它的会话就是以这个身份记录和恢复的 (#660)。
            agent = await IdentityService(session).ensure_topic_agent_user(topic_id)
            await session.commit()
            return lease.device_id, agent.id, agent.username
