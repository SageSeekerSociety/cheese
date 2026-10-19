"""「我的设备 / Agent」管理：机主管理自己登记的机器。

原来住在单文件 ``app/api/routes/connector.py`` 的这一段；门面在
``app/api/routes/connector/__init__.py``。每台设备是纯算力（没有 ``agent_handle``），
真正跑在上面的 agent 是逐个 screen 报上来的。请求与响应模型在
``app/api/routes/connector/schemas.py``。
"""

import logging
import re
import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.routes.connector.devices import DeviceServiceDep
from app.api.routes.connector.schemas import (
    AccessibleDevicesOut,
    AccountConnectionIn,
    AccountConnectionOut,
    ClaudeCodeLoginOut,
    ConnectionRename,
    ConnectionModels,
    ModelConnectionsOut,
    RenameDeviceRequest,
)
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.sentences import say
from app.domain.agent import model_connections
from app.domain.agent.model_catalog import model_choices
from app.domain.agent.device_hub import DeviceOffline, HubScreen, device_hub
from app.domain.agent.harness.claude_code import executor_launch, owner_login
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.own import rename_for_connection
from app.domain.device import sandbox
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply
from app.domain.machine.session_work import screen_agent_name
from app.domain.team.repositories import TeamRepository
from app.domain.usage.services import UsageService

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)


class StartClaudeLoginIn(BaseModel):
    team_handle: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class StartClaudeLoginOut(BaseModel):
    opened: bool
    command: str | None


@router.post(
    "/teams/{team_id}/devices/{device_id}/claude-login", dependencies=[ROUTE_DECIDES]
)
async def start_team_claude_login(
    team_id: int,
    device_id: str,
    body: StartClaudeLoginIn,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> StartClaudeLoginOut:
    await _team_access(db, resolver, team_id, manage=True)
    device = await service.get_hosted_device(device_id)
    if device is None or device.team_id != team_id:
        raise NotFoundError(say("deviceNotFound"))
    if not device_hub.is_online(device_id):
        raise ConflictError(say("modelServiceMachineOffline"))
    # The connector supplies its own --config, so the terminal command names
    # this enrollment, including profiles whose team was enrolled more than once.
    status = await device_hub.exec(device_id, ["cheesehost", "status"], timeout=15)
    profile = re.search(
        r"^\s*profile\s+([A-Za-z0-9_-]+)\s*$", status.get("stdout", ""), re.MULTILINE
    )
    command = f"cheesehost --profile {profile[1]} claude login" if profile else None
    page = f"/teams/{body.team_handle}/models?" + urlencode({"loginDevice": device_id})
    link = "cheese://open?" + urlencode({"path": page})
    system = device_hub.target(device_id).partition("-")[0]
    argv = {
        "darwin": ["open", link],
        "windows": ["rundll32", "url.dll,FileProtocolHandler", link],
        "linux": ["xdg-open", link],
    }.get(system)
    if argv is None:
        return StartClaudeLoginOut(opened=False, command=command)
    try:
        result = await device_hub.exec(device_id, argv, timeout=15)
    except (DeviceOffline, TimeoutError):
        return StartClaudeLoginOut(opened=False, command=command)
    return StartClaudeLoginOut(opened=result.get("exit") == 0, command=command)


async def _require_user(resolver: ActorResolverDep) -> int:
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError(say("deviceManageSignIn"))
    return actor.user_id


def _screens_by_device() -> dict[str, list[HubScreen]]:
    """Every online screen on the platform, grouped by its device: one pass over
    the hub per request, so a list of N devices does not scan it N times."""

    by_device: dict[str, list[HubScreen]] = {}
    for screen in device_hub.all_online_screens():
        by_device.setdefault(screen.device_id, []).append(screen)
    return by_device


async def _device_screens(
    db: AsyncSession, screens: list[HubScreen]
) -> list[dict[str, Any]]:
    """One device's currently-open screens (agents), already picked out by
    ``_screens_by_device``, for the UI to open their 现场, each with the name its
    agent goes by."""

    out: list[dict[str, Any]] = []
    for screen in screens:
        out.append(
            {
                "sid": screen.sid,
                "agent_handle": screen.agent_handle,
                "agent_user_id": str(screen.agent_user_id),
                "project_id": str(screen.project_id) if screen.project_id else None,
                "topic_id": str(screen.topic_id) if screen.topic_id else None,
                **await screen_agent_name(
                    db, screen.project_id, screen.topic_id, screen.agent_handle
                ),
            }
        )
    return out


async def _device_view(
    db: AsyncSession, device: Device, screens: list[HubScreen]
) -> dict[str, Any]:
    # A device is pure compute — no ``agent_handle`` here. The agents actually running
    # on it are the per-screen entries (each carries its own agent), surfaced below.
    return {
        "device_id": device.device_id,
        "name": device.name,
        "online": device_hub.is_online(device.device_id),
        "team_id": device.team_id,
        "last_seen_at": device.last_seen_at.isoformat()
        if device.last_seen_at
        else None,
        "screens": await _device_screens(db, screens),
    }


@router.get("/my/devices")
async def my_devices(
    resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any]:
    """List the devices the logged-in human owns, with liveness + their open agents."""
    user_id = await _require_user(resolver)
    devices = await service.list_enrolled_by(user_id)
    by_device = _screens_by_device()
    views = []
    for device in devices:
        view = await _device_view(db, device, by_device.get(device.device_id, []))
        if device.supply == Supply.self_hosted:
            # Whether it can give a session a sandbox, and if not the machine's
            # own reason, worded for its owner: theirs to act on.
            view["sandbox"] = sandbox.status(
                await service.sandbox_check(device.device_id)
            )
        # Its system, as its connector last said (`windows`, `darwin`,
        # `linux`), for the page to name a command that runs there; None
        # while it has not been heard from.
        view["system"] = device_hub.target(device.device_id).partition("-")[0] or None
        views.append(view)
    return {"devices": views}


# Connector lists return a raw object, without the platform response envelope.
@router.get(
    "/my/team-devices",
    response_model=None,
    responses={200: {"model": AccessibleDevicesOut}},
)
async def accessible_devices(
    resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any]:
    """Enrollments visible through current team membership, for the desktop app."""
    user_id = await _require_user(resolver)
    devices = []
    for team in await TeamRepository(db).list_teams_of_user(user_id):
        devices.extend(await service.list_devices_for_team(team.id))
    return {"devices": [await _device_view(db, d, []) for d in devices]}


async def _my_self_hosted(
    device_id: str, resolver: ActorResolverDep, service: DeviceService
) -> None:
    """Only the machine's owner, and only for a machine they enrolled."""
    user_id = await _require_user(resolver)
    device = await service.get_device(device_id)
    if device is None or device.supply != Supply.self_hosted:
        raise NotFoundError(say("deviceNotFound"))
    if not await service.may_manage(device, user_id):
        raise ForbiddenError(say("deviceTeamAdminOnly"))


async def _team_access(db, resolver, team_id: int, *, manage: bool = False) -> int:
    user_id = await _require_user(resolver)
    teams = TeamRepository(db)
    if not await teams.is_team_member(team_id, user_id):
        raise ForbiddenError(say("notTeamMember"))
    if manage and not await teams.is_team_at_least_admin(team_id, user_id):
        raise ForbiddenError(say("deviceTeamAdminOnly"))
    return user_id


async def _team_login(db, resolver, service, team_id: int, login_id: str) -> str:
    await _team_access(db, resolver, team_id, manage=True)
    device_id = await owner_login.machine_of(db, login_id)
    device = await service.get_device(device_id) if device_id else None
    if device is None or device.team_id != team_id:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    return device.device_id


def _login_out(login: dict | None) -> ClaudeCodeLoginOut | None:
    return None if login is None else ClaudeCodeLoginOut.model_validate(login)


@router.get("/teams/{team_id}/model-connections")
async def team_model_connections(
    team_id: int, resolver: ActorResolverDep, db: DbSession
) -> ModelConnectionsOut:
    """The caller's model connections (#2991): the logins on the machines they
    enrolled — what each logs in with, where it is kept, its last check — and
    the API keys and model services their account holds. Never a key."""
    user_id = await _team_access(db, resolver, team_id)
    connections = await model_connections.of_team(db, team_id, device_hub)
    held = await account_connections.of_team(db, team_id)
    access = await UsageService(db).model_access(team_id)
    return ModelConnectionsOut.model_validate(
        {
            "can_manage": await TeamRepository(db).is_team_at_least_admin(
                team_id, user_id
            ),
            "platform_models": [
                m for m in model_choices({}) if access.shows(m) and access.allows(m)
            ],
            "connections": connections,
            "account_connections": [account_connections.view(r) for r in held],
        }
    )


async def _team_account_connection(
    db, resolver, team_id: int, connection_id: uuid.UUID
):
    await _team_access(db, resolver, team_id, manage=True)
    row = await account_connections.owned(db, team_id, connection_id)
    if row is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    return row


# The handler decides: a person keeps a connection on their own account.
@router.post("/teams/{team_id}/model-connections", dependencies=[ROUTE_DECIDES])
async def add_account_connection(
    team_id: int, body: AccountConnectionIn, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Keep an Anthropic API key or a model service on the caller's account,
    with no machine (#2991). The service is called once first; a connection
    it refuses is not kept, and the refusal says why. The key is sealed and
    never returned."""
    await _team_access(db, resolver, team_id, manage=True)
    row = await account_connections.create(
        db,
        team_id,
        kind=body.kind,
        allowed_models=list(dict.fromkeys(body.allowed_models)),
        key=body.key,
        base_url=body.base_url,
        model=body.model,
        name=body.name,
    )
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.post(
    "/teams/{team_id}/model-connections/{connection_id}/check",
    dependencies=[ROUTE_DECIDES],
)
async def check_account_connection(
    team_id: int, connection_id: uuid.UUID, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Call the service again with the connection, and record what it said."""
    row = await _team_account_connection(db, resolver, team_id, connection_id)
    await account_connections.check(db, row)
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.patch(
    "/teams/{team_id}/model-connections/{connection_id}", dependencies=[ROUTE_DECIDES]
)
async def rename_account_connection(
    team_id: int,
    connection_id: uuid.UUID,
    body: ConnectionRename,
    resolver: ActorResolverDep,
    db: DbSession,
) -> AccountConnectionOut:
    """Name a connection; the own agents on it are named after it."""
    row = await _team_account_connection(db, resolver, team_id, connection_id)
    row.name = body.name or None
    await db.flush()
    await rename_for_connection(db, account_connection_id=row.id)
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.delete(
    "/teams/{team_id}/model-connections/{connection_id}", dependencies=[ROUTE_DECIDES]
)
async def remove_account_connection(
    team_id: int, connection_id: uuid.UUID, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Remove a connection the caller's account holds: its key is deleted at
    once, and nothing starts with it again. The own agents on it stay in their
    projects with no connection, as one on a removed machine login does.
    Answers with what was removed."""
    row = await _team_account_connection(db, resolver, team_id, connection_id)
    removed = AccountConnectionOut.model_validate(account_connections.view(row))
    await db.delete(row)
    await db.commit()
    return removed


@router.post("/my/devices/{device_id}/claude-code", dependencies=[ROUTE_DECIDES])
async def check_my_claude_code(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> None:
    """Refresh device credentials for the team Models page without a device summary."""
    await _my_self_hosted(device_id, resolver, service)
    await _ask_again(db, device_id)


async def _ask_again(db: DbSession, device_id: str) -> None:
    if not device_hub.is_online(device_id):
        return
    try:
        await owner_login.settle(db, device_hub, device_id)
        logins = await owner_login.ask(device_hub, device_id)
    except Exception:
        # Gone or slow to answer: what it said last still stands.
        logger.warning("claude login not checked on %s", device_id, exc_info=True)
        return
    await owner_login.remember(db, device_id, logins)
    await db.commit()


# The handler decides: only the machine's owner, a person, may ask it.
@router.post(
    "/teams/{team_id}/claude-logins/{login_id}/check", dependencies=[ROUTE_DECIDES]
)
async def check_my_claude_login(
    team_id: int,
    login_id: str,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut | None:
    """Ask the machine holding one of the caller's logins again what it is
    (「模型连接」's 重新检查). The machine answers for all of its logins at once."""
    await _ask_again(db, await _team_login(db, resolver, service, team_id, login_id))
    return _login_out(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may remove it.
@router.delete(
    "/teams/{team_id}/claude-logins/{login_id}", dependencies=[ROUTE_DECIDES]
)
async def revoke_my_claude_login(
    team_id: int,
    login_id: str,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut | None:
    """Log one of the caller's own Claude Code logins out on its machine, from
    any browser (#2991); the machine's other logins stay. Out of use at once;
    the machine logs out now when online, else as it next connects."""
    await _team_login(db, resolver, service, team_id, login_id)
    await owner_login.revoke(db, device_hub, login_id)
    return _login_out(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may name it.
@router.patch("/teams/{team_id}/claude-logins/{login_id}", dependencies=[ROUTE_DECIDES])
async def rename_my_claude_login(
    team_id: int,
    login_id: str,
    body: ConnectionRename,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut:
    """Name one of the caller's own Claude Code logins (#2991); the own agents
    on it are named after it, whatever the machine reports later. The machine
    need not be online."""
    await _team_login(db, resolver, service, team_id, login_id)
    if not await owner_login.rename(db, login_id, body.name):
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await db.commit()
    return ClaudeCodeLoginOut.model_validate(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may ask it.
@router.post("/my/devices/{device_id}/sandbox", dependencies=[ROUTE_DECIDES])
async def check_my_sandbox(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any] | None:
    """Ask one of the caller's machines again whether it can give a session a
    sandbox: after its owner changed what it lacked, the page asks rather than
    leaving the answer from its last connection standing."""
    user_id = await _require_user(resolver)
    device = await service.get_hosted_device(device_id)
    if device is None:
        raise NotFoundError(say("deviceNotFound"))
    if not await service.may_manage(device, user_id):
        raise ForbiddenError(say("deviceTeamAdminOnly"))
    if device_hub.is_online(device_id):
        try:
            await sandbox.refresh(
                service, device_hub, device, executor_launch.sandbox_check_script()
            )
            await db.commit()
        except Exception:
            # Gone or slow to answer: what it said last still stands.
            logger.warning("sandbox not checked on %s", device_id, exc_info=True)
    return sandbox.status(await service.sandbox_check(device_id))


@router.patch("/my/devices/{device_id}", dependencies=[ROUTE_DECIDES])
async def rename_my_device(
    device_id: str,
    body: RenameDeviceRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Rename a device the caller owns (the service enforces ownership)."""
    user_id = await _require_user(resolver)
    device = await service.rename(device_id, body.name, actor_user_id=user_id)
    return await _device_view(db, device, _screens_by_device().get(device_id, []))


@router.delete("/my/devices/{device_id}", dependencies=[ROUTE_DECIDES])
async def unbind_my_device(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep
) -> dict[str, Any]:
    """Unbind (forget) a device the caller owns — its durable token stops working."""
    user_id = await _require_user(resolver)
    await service.remove(device_id, actor_user_id=user_id)
    return {"deleted": True, "device_id": device_id}


@router.get("/teams/{team_id}/devices")
async def team_devices(
    team_id: int,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    user_id = await _require_user(resolver)
    teams = TeamRepository(db)
    if not await teams.is_team_member(team_id, user_id):
        raise ForbiddenError(say("notTeamMember"))
    devices = await service.list_devices_for_team(team_id)
    may_manage = await teams.is_team_at_least_admin(team_id, user_id)
    by_device = _screens_by_device()
    return {
        "can_manage": may_manage,
        "devices": [
            await _device_view(db, d, by_device.get(d.device_id, [])) for d in devices
        ],
    }


@router.put(
    "/teams/{team_id}/model-connections/{connection_id}/models",
    dependencies=[ROUTE_DECIDES],
)
async def set_connection_models(
    team_id: int,
    connection_id: uuid.UUID,
    body: ConnectionModels,
    resolver: ActorResolverDep,
    db: DbSession,
) -> AccountConnectionOut:
    row = await _team_account_connection(db, resolver, team_id, connection_id)
    row.allowed_models = list(dict.fromkeys(body.allowed_models))
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


@router.put(
    "/teams/{team_id}/claude-logins/{login_id}/models", dependencies=[ROUTE_DECIDES]
)
async def set_login_models(
    team_id: int,
    login_id: str,
    body: ConnectionModels,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ModelConnectionsOut:
    await _team_login(db, resolver, service, team_id, login_id)
    await owner_login.set_models(db, login_id, body.allowed_models)
    await db.commit()
    return await team_model_connections(team_id, resolver, db)
