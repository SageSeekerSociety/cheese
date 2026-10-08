"""Dependency injection wiring."""

import uuid
from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.db import async_session_factory, engine, get_db
from app.core.ownership import Ownership
from app.core.redis import get_redis_client

# Registers the listener that publishes committed platform notices live.
from app.domain.agent import live_notices as live_notices
from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel
from app.domain.agent.compute import ComputePool, build_compute_pool
from app.domain.agent.device_hub import device_hub
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.pending_messages import bind_runner
from app.domain.agent.profiles import ProfileRegistry, build_registry
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.host import SessionHost
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService
from app.domain.passkey.repositories import PasskeyRepository
from app.domain.passkey.services import PasskeyService
from app.domain.space.services import SpaceLabels
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import UserAuthService

__all__ = [
    "get_db",
    "get_chat_service",
    "get_consumptions",
    "get_session_host",
    "get_profile_registry",
    "get_broker",
    "get_work_runner",
    "get_passkey_service",
    "get_user_realname_service",
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
def get_session_host() -> SessionHost:
    """The sessions on the session host this process talks to — a room's, a
    person's 芝士, a document thread's. One per process, like the chat
    service: it remembers which sessions are running and where each is read."""
    return SessionHost(device_hub)


@lru_cache
def get_consumptions() -> Consumptions:
    """The questions asked of sessions that this process reads to the end, and
    takes over from a process that went away: a person's, a comment
    thread's, a document box's. One per process, beside the session host."""
    return consumptions_for(get_session_host(), get_chat_service())


def consumptions_for(
    host: SessionHost,
    chat: ChatService,
    *,
    redis: Callable[[], Redis | None] = get_redis_client,
    sessions: async_sessionmaker[AsyncSession] = async_session_factory,
) -> Consumptions:
    """The questions read on ``host``, each kind served by its consumer."""
    from app.domain.agent.document import box, thread
    from app.domain.agent.personal import service as personal

    consumptions = Consumptions(host, redis)
    consumptions.serve(personal.KIND, personal.Answers(sessions, consumptions, redis))
    consumptions.serve(thread.KIND, thread.Replies(chat, chat.session_factory))
    consumptions.serve(box.KIND, box.Answers(chat, consumptions))
    return consumptions


@lru_cache
def get_compute_pool() -> ComputePool:
    """The rooms' sessions this process reads, on every machine pool and
    harness: the chat service's, and the ones a runner's ring wakes. Every
    seat's attach checks the work runner's ``owns_sessions`` flag inside its
    seat lock first (FB-56)."""
    cloud = CloudChannel(
        configured=bool(
            settings.microcloud_base_url and settings.microcloud_tenant_secret
        ),
    )
    pool = build_compute_pool(cloud_channel=cloud, host=get_session_host())
    runner = get_work_runner()
    for runtime in pool._runtimes():
        runtime.bind_owns_sessions(lambda: runner)
    return pool


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(
        session_factory=async_session_factory,
        base_system_prompt=settings.agent_system_prompt,
        workspace_root=settings.workspace_root,
        profiles=get_profile_registry(),
        compute=get_compute_pool(),
        gateway=get_llm_gateway(),
    )


@lru_cache
def get_ownership() -> Ownership:
    """This process's claim on the running work (`app.core.ownership`)."""
    return Ownership(engine.url.render_as_string(hide_password=False))


@lru_cache
def get_work_runner() -> AgentWorkRunner:
    # #388 缺陷一: let the cold-start fuse know when a topic's device screen is
    # running on a credential the backend already stamped as expired, so a doomed
    # turn fast-fails with the true reason instead of burning the full fuse. Reads
    # the device hub's live screen state (in-memory, cheap); a topic on the local
    # tmux/SDK path has no screen there → None → the fuse is unchanged.
    from app.domain.agent.admission import HostMemory
    from app.domain.agent.device_provider import topic_credential_expiry

    runner = AgentWorkRunner(
        get_broker(),
        turn_timeout_s=settings.agent_turn_timeout_s,
        first_output_timeout_s=settings.agent_first_output_timeout_s,
        credential_expiry_of=topic_credential_expiry,
        host_has_room=HostMemory().has_room,
    )
    bind_runner(runner)
    return runner


async def get_user_auth_service(
    db=Depends(get_db),
) -> UserAuthService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    return UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        stats_repo=stats_repo,
    )


async def get_oauth_service(
    db=Depends(get_db),
) -> OAuthService:
    return OAuthService(repo=OAuthConnectionRepository(session=db))


async def get_user_realname_service(
    db=Depends(get_db),
) -> UserRealNameService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    return UserRealNameService(
        session=db,
        user_repo=user_repo,
        profile_repo=profile_repo,
        realname_repo=realname_repo,
        space_labels=SpaceLabels(session=db),
    )


async def get_passkey_service(
    db=Depends(get_db),
) -> PasskeyService:
    repo = PasskeyRepository(session=db)
    return PasskeyService(repo=repo)
