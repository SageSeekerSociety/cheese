"""「我的设备 / Agent」管理：机主管理自己登记的机器。

原来住在单文件 ``app/api/routes/connector.py`` 的这一段；门面在
``app/api/routes/connector/__init__.py``。每台设备是纯算力（没有 ``agent_handle``），
真正跑在上面的 agent 是逐个 screen 报上来的。请求与响应模型在
``app/api/routes/connector/schemas.py``。
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter
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
    ModelConnectionsOut,
    ModelServiceRequest,
    RenameDeviceRequest,
)
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.agent import model_connections
from app.domain.agent.device_hub import DeviceOffline, HubScreen, device_hub
from app.domain.agent.harness.claude_code import executor_launch, owner_login
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.own import rename_for_connection
from app.domain.device import sandbox
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply
from app.domain.machine.session_work import device_users, screen_agent_name
from app.domain.team.repositories import TeamRepository

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)


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
    # Every machine's owner-login in one query, before the loop: one query per
    # self-hosted machine is what this list cost before (#2991).
    logins = await owner_login.statuses(
        db, [d.device_id for d in devices if d.supply == Supply.self_hosted]
    )
    views = []
    for device in devices:
        view = await _device_view(db, device, by_device.get(device.device_id, []))
        # Whether its owner's own Claude Code is logged in there for the
        # platform (#2991): the owner's to see, not the team's. Only a machine
        # they enrolled themselves runs it; a cloud machine says nothing.
        if device.supply == Supply.self_hosted:
            view["claude_code"] = logins.get(device.device_id)
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
    logins = await owner_login.connections(db, [d.device_id for d in devices])
    return {
        "devices": [
            {**await _device_view(db, d, []), "logins": logins.get(d.device_id, [])}
            for d in devices
        ]
    }


async def _my_self_hosted(
    device_id: str, resolver: ActorResolverDep, service: DeviceService
) -> None:
    """Only the machine's owner, and only for a machine they enrolled."""
    user_id = await _require_user(resolver)
    owned = {d.device_id: d for d in await service.list_enrolled_by(user_id)}
    device = owned.get(device_id)
    if device is None or device.supply != Supply.self_hosted:
        raise NotFoundError(say("deviceNotYours"))


async def _my_login(
    db: DbSession, resolver: ActorResolverDep, service: DeviceService, login_id: str
) -> str:
    """The machine holding one of the caller's logins, on a machine they
    enrolled, and only theirs."""
    device_id = await owner_login.machine_of(db, login_id)
    if device_id is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await _my_self_hosted(device_id, resolver, service)
    return device_id


def _login_out(login: dict | None) -> ClaudeCodeLoginOut | None:
    return None if login is None else ClaudeCodeLoginOut.model_validate(login)


@router.get("/my/model-connections")
async def my_model_connections(
    resolver: ActorResolverDep, db: DbSession
) -> ModelConnectionsOut:
    """The caller's model connections (#2991): the logins on the machines they
    enrolled — what each logs in with, where it is kept, its last check — and
    the API keys and model services their account holds. Never a key."""
    user_id = await _require_user(resolver)
    connections = await model_connections.of_owner(db, user_id, device_hub)
    held = await account_connections.of_owner(db, user_id)
    return ModelConnectionsOut.model_validate(
        {
            "connections": connections,
            "account_connections": [account_connections.view(r) for r in held],
        }
    )


async def _my_account_connection(db, resolver, connection_id: uuid.UUID):
    user_id = await _require_user(resolver)
    row = await account_connections.owned(db, user_id, connection_id)
    if row is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    return row


# The handler decides: a person keeps a connection on their own account.
@router.post("/my/model-connections", dependencies=[ROUTE_DECIDES])
async def add_account_connection(
    body: AccountConnectionIn, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Keep an Anthropic API key or a model service on the caller's account,
    with no machine (#2991). The service is called once first; a connection
    it refuses is not kept, and the refusal says why. The key is sealed and
    never returned."""
    user_id = await _require_user(resolver)
    row = await account_connections.create(
        db,
        user_id,
        kind=body.kind,
        key=body.key,
        base_url=body.base_url,
        model=body.model,
        name=body.name,
    )
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.post(
    "/my/model-connections/{connection_id}/check", dependencies=[ROUTE_DECIDES]
)
async def check_account_connection(
    connection_id: uuid.UUID, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Call the service again with the connection, and record what it said."""
    row = await _my_account_connection(db, resolver, connection_id)
    await account_connections.check(db, row)
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.patch("/my/model-connections/{connection_id}", dependencies=[ROUTE_DECIDES])
async def rename_account_connection(
    connection_id: uuid.UUID,
    body: ConnectionRename,
    resolver: ActorResolverDep,
    db: DbSession,
) -> AccountConnectionOut:
    """Name a connection; the own agents on it are named after it."""
    row = await _my_account_connection(db, resolver, connection_id)
    row.name = body.name or None
    await db.flush()
    await rename_for_connection(db, account_connection_id=row.id)
    await db.commit()
    return AccountConnectionOut.model_validate(account_connections.view(row))


# The handler decides: only its owner.
@router.delete("/my/model-connections/{connection_id}", dependencies=[ROUTE_DECIDES])
async def remove_account_connection(
    connection_id: uuid.UUID, resolver: ActorResolverDep, db: DbSession
) -> AccountConnectionOut:
    """Remove a connection the caller's account holds: its key is deleted at
    once, and nothing starts with it again. The own agents on it stay in their
    projects with no connection, as one on a removed machine login does.
    Answers with what was removed."""
    row = await _my_account_connection(db, resolver, connection_id)
    removed = AccountConnectionOut.model_validate(account_connections.view(row))
    await db.delete(row)
    await db.commit()
    return removed


@router.post("/my/devices/{device_id}/claude-code", dependencies=[ROUTE_DECIDES])
async def check_my_claude_code(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any] | None:
    """Ask one of the caller's machines again which logins their own Claude
    Code has there (#2991), and answer the one the machine shows. They log in
    on the machine itself, which tells the server nothing; the page showing
    that machine asks, rather than leaving the answer from its last connection
    standing. A logout its owner asked for while it was away is carried out
    first."""
    await _my_self_hosted(device_id, resolver, service)
    await _ask_again(db, device_id)
    return (await owner_login.statuses(db, [device_id])).get(device_id)


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
@router.post("/my/claude-logins/{login_id}/check", dependencies=[ROUTE_DECIDES])
async def check_my_claude_login(
    login_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> ClaudeCodeLoginOut | None:
    """Ask the machine holding one of the caller's logins again what it is
    (「模型连接」's 重新检查). The machine answers for all of its logins at once."""
    await _ask_again(db, await _my_login(db, resolver, service, login_id))
    return _login_out(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may remove it.
@router.delete("/my/claude-logins/{login_id}", dependencies=[ROUTE_DECIDES])
async def revoke_my_claude_login(
    login_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> ClaudeCodeLoginOut | None:
    """Log one of the caller's own Claude Code logins out on its machine, from
    any browser (#2991); the machine's other logins stay. Out of use at once;
    the machine logs out now when online, else as it next connects."""
    await _my_login(db, resolver, service, login_id)
    await owner_login.revoke(db, device_hub, login_id)
    return _login_out(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may name it.
@router.patch("/my/claude-logins/{login_id}", dependencies=[ROUTE_DECIDES])
async def rename_my_claude_login(
    login_id: str,
    body: ConnectionRename,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut:
    """Name one of the caller's own Claude Code logins (#2991); the own agents
    on it are named after it, whatever the machine reports later. The machine
    need not be online."""
    await _my_login(db, resolver, service, login_id)
    if not await owner_login.rename(db, login_id, body.name):
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await db.commit()
    return ClaudeCodeLoginOut.model_validate(await owner_login.status(db, login_id))


# The handler decides: only the machine's owner, a person, may set it.
@router.put(
    "/my/devices/{device_id}/claude-code/model-service", dependencies=[ROUTE_DECIDES]
)
async def use_my_model_service(
    device_id: str,
    body: ModelServiceRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut | None:
    """Add a login to the caller's own Claude Code on one of their machines
    that calls another model service, from any browser (#2991), and answer it.
    The key is handed to the machine, which keeps it; it is not stored here.
    The machine has to be online."""
    await _my_self_hosted(device_id, resolver, service)
    if not device_hub.is_online(device_id):
        raise ConflictError(say("modelServiceMachineOffline"))
    try:
        local_id = await owner_login.use_model_service(
            db,
            device_hub,
            device_id,
            base_url=body.base_url,
            token=body.token,
            model=body.model,
        )
    except owner_login.MachineRefused as refused:
        raise UnprocessableEntityError(
            say("modelServiceRefused", reason=str(refused))
        ) from None
    except (DeviceOffline, TimeoutError):
        raise ConflictError(say("modelServiceMachineOffline")) from None
    return _login_out(await owner_login.status_on(db, device_id, local_id))


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
    users = await device_users(db, [d.device_id for d in devices]) if may_manage else {}
    logins = await owner_login.connections(db, [d.device_id for d in devices])
    return {
        "can_manage": may_manage,
        "devices": [
            {
                **await _device_view(db, d, by_device.get(d.device_id, [])),
                "in_use": users.get(d.device_id),
                "logins": logins.get(d.device_id, []),
            }
            for d in devices
        ],
    }
