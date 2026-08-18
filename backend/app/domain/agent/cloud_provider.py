"""The concrete MicroCloud compute provider for one-machine-per-topic Cloud."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.agent.device_hub import DeviceHub, device_hub
from app.domain.agent.device_provider import DeviceProvider
from app.domain.agent.hooks_substrate import ScreenSetupError
from app.domain.device.supply import Supply, Visibility
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


class CloudProvider(DeviceProvider):
    """One-topic lease resolution over the existing device transport."""

    name = "cloud"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker | None = None,
        hub: DeviceHub | None = None,
        idle_suspect_s: float = 300.0,
        hard_ceiling_s: float = 10800.0,
        configured: bool,
        ensure_topic_cloud: EnsureTopicCloud,
        read_topic_cloud: ReadTopicCloud,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            hub=hub,
            idle_suspect_s=idle_suspect_s,
            hard_ceiling_s=hard_ceiling_s,
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
        actor: Actor | None,
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
        factory = self._session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
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
                    topic_id, lease.device_id, visibility=Visibility.host
                )
            agent = await IdentityService(session).ensure_topic_agent_user(topic_id)
            await session.commit()
            return lease.device_id, agent.id, agent.username

    async def _precheck(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> tuple[str, int, str]:
        resolved = await self._resolve_device_agent(project_id, topic_id)
        if resolved is None:
            raise ScreenSetupError("Cloud 机器尚未完成连接")
        return resolved
