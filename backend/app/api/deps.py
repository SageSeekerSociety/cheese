"""Dependency injection wiring."""

import uuid
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory, get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel, CloudLease
from app.domain.agent.compute import build_compute_pool
from app.domain.agent.device_hub import device_hub
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.profiles import ProfileRegistry, build_registry
from app.domain.agent.runtime import (
    AgentWorkRunner,
    addressed_to_agent,
    get_broker,
)
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.identity.actor import Actor
from app.domain.machine.models import AiStatus, MachineStatus, ProjectMachine
from app.domain.machine.services import MachineService
from app.domain.machine.wakeup import WAKE_NOTICE, WAKE_PROMPT, CloudWakeup

__all__ = [
    "get_db",
    "get_chat_service",
    "get_profile_registry",
    "get_broker",
    "get_work_runner",
    "get_cloud_wakeup",
    "project_device_online",
    "team_device_online",
]


async def project_device_online(db: AsyncSession, project_id: uuid.UUID) -> bool:
    """Whether this project has an enrolled machine connected right now — the honest
    availability of the self-hosted compute pool for THIS project's context (compute
    belongs to the project/team, not globally, execution-architecture v4). Used by the
    compute-profile routes to scope the 自托管设备 pool to a project's own machines."""
    service = DeviceService(SqlDeviceRepository(db))
    return await service.project_has_online_device(project_id, device_hub.is_online)


async def team_device_online(db: AsyncSession, team_id: int) -> bool:
    """Whether one of the team's registered compute nodes is connected now."""
    service = DeviceService(SqlDeviceRepository(db))
    devices = await service.list_devices_for_team(team_id)
    return any(device_hub.is_online(device.device_id) for device in devices)


@lru_cache
def get_profile_registry() -> ProfileRegistry:
    return build_registry(settings)


def _cloud_lease(machine: ProjectMachine) -> CloudLease:
    error = None
    if machine.status == MachineStatus.error:
        error = "Cloud machine provisioning failed"
    elif machine.ai_status == AiStatus.error:
        error = "Cloud machine AI access provisioning failed"
    return CloudLease(
        project_id=machine.project_id,
        device_id=machine.device_id,
        machine_ready=machine.status == MachineStatus.running,
        # `disabled` is a settled channel too: the built-in AI access is off
        # because the machine reaches the model through the gateway. Enrolment
        # and the wake-up sweep already count it as ready; reading it as "not
        # yet" here made every such room wake, find itself unready, and wait
        # again, once every sweep, forever.
        ai_ready=machine.ai_status in (AiStatus.ready, AiStatus.disabled),
        error=error,
    )


async def _ensure_topic_cloud(topic_id: uuid.UUID, actor: Actor | None) -> CloudLease:
    async with async_session_factory() as session:
        service = MachineService(session)
        if not service.available:
            from app.core.errors import ValidationError

            raise ValidationError(
                "machine provisioning is not configured for this deployment"
            )
        machine = await service.ensure_topic_machine(topic_id, actor=actor)
        await session.commit()
        return _cloud_lease(machine)


async def _read_topic_cloud(topic_id: uuid.UUID) -> CloudLease | None:
    async with async_session_factory() as session:
        machine = await MachineService(session).topic_machine(topic_id)
        await session.commit()
        return None if machine is None else _cloud_lease(machine)


@lru_cache
def get_llm_gateway() -> LlmGateway | None:
    """Gateway admin client (L1/L2 — defined in `app.domain.agent.gateway`): only
    when the pool routes through the self-hosted gateway AND admin creds are
    configured. ``None`` is a supported deployment, not a fault — it means no
    metering, no brake, and a model catalogue that stays on its floor."""
    if settings.llm_gateway_admin_base and settings.llm_gateway_admin_key:
        return LlmGateway(
            settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
        )
    return None


@lru_cache
def get_chat_service() -> ChatService:
    gateway = get_llm_gateway()
    cloud = CloudChannel(
        configured=bool(
            settings.microcloud_base_url and settings.microcloud_tenant_secret
        ),
        ensure_topic_cloud=_ensure_topic_cloud,
        read_topic_cloud=_read_topic_cloud,
    )
    return ChatService(
        session_factory=async_session_factory,
        base_system_prompt=settings.agent_system_prompt,
        workspace_root=settings.workspace_root,
        profiles=get_profile_registry(),
        compute=build_compute_pool(cloud_channel=cloud),
        gateway=gateway,
    )


@lru_cache
def get_cloud_wakeup() -> CloudWakeup:
    """The one object that starts a Cloud topic's held turn — asked by the
    enrollment sweep and by the connector route (see machine/wakeup.py)."""
    from app.domain.agent.platform_notices import SEVERITY_INFO, WHO_PLATFORM

    chat = get_chat_service()

    async def ready_leases(device_id: str) -> list[tuple[uuid.UUID, str]]:
        async with async_session_factory() as session:
            return await MachineService(session).ready_topic_devices(device_id)

    async def deliver_held(topic_id: uuid.UUID) -> None:
        """把房间扣着的那条消息送出去 —— 收件人是它当初点的那个席位。

        房间里看见的那一行先落库，再投递，而且是**等它落完**才投递：这一行不只是
        一句话，它还是这个房间的 Cloud 生命周期从「正在创建」转出去的那条记录
        （`cloud_waiting_topics` 读它的 `state`），也就是「这个房间已经叫醒过了」
        本身。交给这一轮去写，它就落在算力闸的后面 —— 算力用尽那一轮直接被拒，记
        录永远不写，房间永远停在 waiting，于是每一拍扫描、每一次连接器挂上来都再
        投递一次，房间里堆出一串「算力用尽」；就算不撞闸，`submit` 是当场返回的，
        记录要等这一轮排到队才写，这中间的扫描和连接器会为同一个房间起第二轮。

        `WAKE_PROMPT` 只作为提示词进到 agent 那边，不进时间线：平台在房间里说的每
        一句都是系统事件，没有一条冒充人说的话。所以这一轮不再自带开场白
        （`nudge_event` 留空）—— 开场白已经在上面写好了，一件事一条记录。
        """
        from app.domain.topic_membership.services import addressable_seat

        seat = await addressable_seat(async_session_factory, topic_id)
        turn_id = uuid.uuid4()
        block = await chat.post_system_event(
            topic_id,
            WAKE_NOTICE,
            turn_id,
            meta={
                "event_type": "cloud_provisioning",
                "state": "ready",
                "severity": SEVERITY_INFO,
                "who": WHO_PLATFORM,
            },
        )
        if block is None:
            return  # 房间没了，没有什么可送
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": block}
        )
        get_work_runner().submit(
            chat,
            topic_id,
            author="system",
            content=WAKE_PROMPT,
            addressed=addressed_to_agent(seat),
            turn_id=turn_id,
        )

    async def announce_failure(topic_id: uuid.UUID, text: str) -> None:
        from app.domain.agent.platform_notices import SEVERITY_ERROR, WHO_HUMAN

        block = await chat.post_system_event(
            topic_id,
            text,
            meta={
                "event_type": "cloud_provisioning",
                "state": "failed",
                "severity": SEVERITY_ERROR,
                "who": WHO_HUMAN,
                "detail": (
                    "这条消息还在，平台不会自动换一台机器。"
                    "可以在项目设置里查看这台设备的状态，处理后重试。"
                ),
                "detail_label": "下一步",
                "retryable": True,
            },
        )
        if block is not None:
            await get_broker().publish(
                str(topic_id), {"type": "event_block", "block": block}
            )

    return CloudWakeup(
        ready_leases=ready_leases,
        waiting_topics=chat.cloud_waiting_topics,
        deliver_held=deliver_held,
        is_online=device_hub.is_online,
        announce_failure=announce_failure,
    )


@lru_cache
def get_work_runner() -> AgentWorkRunner:
    # #388 缺陷一: let the cold-start fuse know when a topic's device screen is
    # running on a credential the backend already stamped as expired, so a doomed
    # turn fast-fails with the true reason instead of burning the full fuse. Reads
    # the device hub's live screen state (in-memory, cheap); a topic on the local
    # tmux/SDK path has no screen there → None → the fuse is unchanged.
    from app.domain.agent.device_provider import topic_credential_expiry

    runner = AgentWorkRunner(
        get_broker(),
        turn_timeout_s=settings.agent_turn_timeout_s,
        first_output_timeout_s=settings.agent_first_output_timeout_s,
        credential_expiry_of=topic_credential_expiry,
    )
    runner.subscribe_messages()
    return runner
