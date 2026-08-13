"""The self-hosted device connector: device flow + the frozen link.Msg WS (P3).

Endpoints the frozen cli expects (it dials ``base = …/connector``):

* ``POST /connector/auth/device/start`` — begin the device flow; returns the
  ``device_code`` + an ``approve_url`` a human opens, plus the poll ``interval``.
* ``POST /connector/auth/device/poll``  — the client polls; ``pending`` until a human
  approves, then the durable device token + id.
* ``POST /connector/connect``           — the human approval (behind their login):
  mints an agent-user + a ``device`` binding, binds the device to that owner, and
  (optionally) assigns it to a project.
* ``WS   /connector/agent``             — the device's dial-out control channel: the
  device authenticates with its durable token and every ``link.Msg`` is dispatched to
  the shared ``DeviceHub``.

The DB-backed device flow uses a per-request ``SqlDeviceRepository``; the WS + hub are
process-global (``device_hub``). Device-hosted ``claude`` posts its Claude Code hooks to
the existing ``/sandbox/hooks/{topic_id}`` endpoint (scoped-token auth + shared
``hook_router``) — the connector adds no second hook path.
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.tokens import verify_session_token
from app.domain.agent.device_hub import HubScreen, ViewerTransport, device_hub
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply, Visibility
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.team.repositories import TeamRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

router = APIRouter(prefix="/connector", tags=["connector"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_device_service(db: DbSession) -> DeviceService:
    """The per-request device service (SQL-backed by default). Exposed as an
    overridable dependency so a standalone spike / test can inject an in-memory repo
    while reusing these exact routes."""
    return DeviceService(SqlDeviceRepository(db))


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


# --- request/response schemas --------------------------------------------------


class DeviceStartRequest(BaseModel):
    device_name: str | None = None


class DevicePollRequest(BaseModel):
    device_code: str


class ConnectRequest(BaseModel):
    device_code: str
    # The human-chosen name for this compute node (optional — blank keeps the name the
    # cli proposed at device-flow start, avoiding an "unnamed" node).
    device_name: str | None = None
    project_id: uuid.UUID | None = None
    # #282 §四 / #358 · the honest UI line 「让它看到整台机器（能操作这台机器上的服务
    # 和其他房间）」. Whole-machine (visibility=host) is 申请制: OFF by default, so a
    # human enrolling their own persistent box never grants it whole-machine access
    # by omission. The approval page ticks this only when the approver deliberately
    # wants the agent to operate the whole host. Left False → isolated (the boxed
    # default; its per-room-container transport lands in #358 step 2).
    whole_machine: bool = False


# --- device flow ---------------------------------------------------------------


@router.post("/auth/device/start")
async def device_start(
    body: DeviceStartRequest, service: DeviceServiceDep
) -> dict[str, Any]:
    """Begin a device flow. The ``approve_url`` is built from the public base so a
    human can open it and approve this machine (fusion-design §5 device flow)."""
    code = await service.start(body.device_name)
    # The approve link points a human at the frontend ``/connect`` approval page: there,
    # behind login, they bind this machine to a project and mint its agent (item 3).
    # It MUST be the frontend origin (the SPA serves ``/connect``), not
    # ``connector_public_base`` — that's the backend/webhook base (default
    # localhost:8099, the dead pre-merge cheesex port) and would 404 in a browser.
    base = settings.frontend_url.rstrip("/")
    approve_url = f"{base}/connect?code={code}"
    return {"device_code": code, "approve_url": approve_url, "interval": 2}


@router.post("/auth/device/poll")
async def device_poll(
    body: DevicePollRequest, service: DeviceServiceDep
) -> dict[str, Any]:
    """Poll a pending flow: ``{status}`` until approved, then the durable token +
    device id the client persists."""
    return await service.poll(body.device_code)


@router.get("/auth/device/proposed-name")
async def device_proposed_name(
    code: str, service: DeviceServiceDep
) -> dict[str, str | None]:
    """The name the cli proposed for a pending ``code`` (this machine's hostname) so
    the approval page can prefill it — editable. Behind the code (the approval secret),
    same trust level as start/poll; ``null`` when the code is unknown/expired."""
    return {"device_name": await service.code_device_name(code)}


@router.post("/connect")
async def device_connect(
    body: ConnectRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Approve a device on behalf of the logged-in human (fusion-design §4: approve is
    behind a login). Mints an agent-user + ``device`` binding, binds the device to the
    owner, and optionally assigns it to ``project_id``."""
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError("Approving a device requires a logged-in user")

    # Approve binds the device to its owner + mints the durable token. The device is
    # PURE COMPUTE (execution-architecture v3: a ComputePool node) — enrolling a machine
    # does NOT mint an agent. The agent a screen runs as is resolved per project/topic
    # at turn time (fusion-design §5: agent = screen), independent of the host.
    device = await service.approve(
        body.device_code,
        owner_user_id=actor.user_id,
        # 入口决定待遇 (#282 决定 2): a human ran the connector on a machine they
        # already keep running, so the platform may never stop or destroy it —
        # only stop using it. A CONSTANT here, the mirror of the MicroCloud
        # enrolment sweep's `Supply.cloud`.
        supply=Supply.self_hosted,
        # #358: whole-machine visibility is an explicit opt-in, never the default.
        # Only when the approver ticked 「让它看到整台机器」 does this box become
        # host-visible (bare-on-host, sees every room + the host's services);
        # otherwise it enrols as the boxed default (isolated).
        visibility=Visibility.host if body.whole_machine else Visibility.isolated,
        name=body.device_name,
    )
    if body.project_id is not None:
        await service.assign_to_project(
            device.device_id, body.project_id, actor_user_id=actor.user_id
        )
    return {
        "device_id": device.device_id,
        "device_name": device.name,
        "project_id": str(body.project_id) if body.project_id else None,
    }


# --- the device control channel (frozen link.Msg) ------------------------------


class _WebSocketDeviceTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``DeviceTransport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_json(self, msg: dict[str, Any]) -> None:
        await self._websocket.send_json(msg)


@router.websocket("/agent")
async def agent_socket(
    websocket: WebSocket,
    service: DeviceServiceDep,
    db: DbSession,
    x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
    token: str | None = Query(default=None),
) -> None:
    """The device's dial-out control channel. Authenticates with the durable device
    token (header, or ``?token=`` for browsers), then dispatches every inbound
    ``link.Msg`` to the shared ``DeviceHub`` (which drives outbound messages)."""
    device = await service.verify_token(x_cheese_session or token or "")
    # End the auth read-transaction NOW, before the (device-lifetime) receive loop.
    # A ``Depends(get_db)`` session injected into a WebSocket route is only finalized
    # when the socket CLOSES — so without this commit, ``verify_token``'s transaction
    # sits `idle in transaction` for the machine's entire uptime (observed: 2.8h),
    # holding an AccessShareLock on ``device_team`` that made an ALTER TABLE (ACCESS
    # EXCLUSIVE) on the device tables queue behind it until it timed out → site-wide
    # brownout (#356). Committing returns the connection to the pool (lock released)
    # for the life of the connection; ``db`` is the same session ``service`` used
    # (FastAPI caches ``get_db`` across both), and the resolved ``device`` is a plain
    # dataclass, so nothing lazy-loads after the commit.
    await db.commit()
    if device is None:
        # A token is necessary here (never sufficient; screen-scoped calls are
        # authorized per-actor). Reject with policy-violation.
        await websocket.close(code=1008, reason="unknown or missing device token")
        return
    await websocket.accept()
    transport = _WebSocketDeviceTransport(websocket)
    await device_hub.attach_device(device.device_id, transport)  # sends welcome{v}
    try:
        while True:
            message = await websocket.receive_json()
            await device_hub.on_device_message(device.device_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        await device_hub.detach_device(device.device_id, transport)


# --- 现场 viewer: a browser watches a device screen's real terminal (read-only) ----


class _WebSocketViewerTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``ViewerTransport`` — it only
    ever *receives* raw screen bytes (the 现场 is relayed byte-for-byte, unparsed)."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)


async def _may_view_screen(
    session: AsyncSession, screen: HubScreen, token: str | None
) -> bool:
    """Only a logged-in human who shares the screen's project (a project member/owner)
    or its topic (a topic-roster member) may watch its 现场 (P1 authz shape). Browsers
    can't set an Authorization header on a WS, so the token rides as ``?token=``.
    """
    if not token:
        return False
    claims = verify_session_token(token)
    if claims is None:
        return False
    # main-minted tokens carry the int user id in ``sub`` and the handle in the
    # ``handle`` claim; membership is keyed by handle, so prefer it (mirrors
    # _token_verifier / the rest of the auth layer). Falling back to ``sub`` keeps
    # legacy cheesex handle-in-sub tokens working.
    handle = claims["handle"] or claims["sub"]
    if screen.project_id is not None:
        if await MemberRepository(session).get(
            project_id=screen.project_id, user_handle=handle
        ):
            return True
        project = await ProjectRepository(session).get(screen.project_id)
        if project is not None and project.owner_handle == handle:
            return True
    if screen.topic_id is not None:
        if await TopicMembershipRepository(session).get(
            topic_id=screen.topic_id, member_handle=handle
        ):
            return True
    return False


def _as_int(value: object, default: int) -> int:
    """Best-effort terminal dimension from an untrusted browser frame — a malformed
    cols/rows must never crash the viewer socket."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n if 0 < n <= 10000 else default


@router.websocket("/session/{sid}/screen")
async def viewer_socket(
    websocket: WebSocket,
    sid: str,
    db: DbSession,
    token: str | None = Query(default=None),
) -> None:
    """Relay one browser terminal ↔ one device screen, byte-for-byte.

    Frames split by type: TEXT is control (JSON ``resize``), BINARY is keystrokes
    forwarded to the pane. Keeping input on the binary channel means a control
    message can never be mistaken for typing, or the reverse.

    Input is deliberate, not incidental: whoever can type here can run anything on
    that machine as the agent's user. It is gated by the SAME check as watching —
    a logged-in member/owner of the screen's project, or a member of its topic —
    because that is already the trust boundary the agent itself runs inside, and a
    member who wants a shell there can otherwise just ask the agent for one.
    Nothing is forwarded before the viewer has attached, so keystrokes cannot
    reach a pane that was never subscribed.

    Unknown-screen and not-authorized close identically (1008) so a screen id
    can't be enumerated."""
    screen = device_hub.screen(sid)
    authorized = screen is not None and await _may_view_screen(db, screen, token)
    # Release the authz read-transaction before the viewer's (long-lived) relay loop.
    # A WS-injected ``get_db`` session lives until the socket closes, so leaving the
    # transaction open would park it `idle in transaction` for the whole view — the
    # same #356 footgun the device control channel hit (an idle-in-txn read lock
    # blocking device/topic-table migrations). Unknown-screen and not-authorized still
    # close identically (1008) so a screen id can't be enumerated.
    await db.commit()
    if screen is None or not authorized:
        await websocket.close(code=1008, reason="cannot view this screen")
        return

    await websocket.accept()
    transport: ViewerTransport = _WebSocketViewerTransport(websocket)
    device_id = screen.device_id
    attached = False  # attach (and subscribe) only once the viewer reports its size
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                data = message.get("bytes")
                # Before the first resize there is no subscription on the device,
                # so there is nothing to type into yet.
                if data and attached:
                    await device_hub.viewer_input(device_id, sid, data)
                continue
            try:
                ctrl = json.loads(text)
            except ValueError:
                continue
            if not isinstance(ctrl, dict) or ctrl.get("type") != "resize":
                continue
            cols = _as_int(ctrl.get("cols"), 120)
            rows = _as_int(ctrl.get("rows"), 32)
            if not attached:
                # First size for this viewer: attach + (if first viewer) subscribe at
                # the real size, matching the frozen web-claude contract.
                await device_hub.attach_viewer(
                    device_id, sid, transport, cols=cols, rows=rows
                )
                attached = True
            else:
                await device_hub.viewer_resize(device_id, sid, cols, rows)
    except WebSocketDisconnect:
        pass
    finally:
        if attached:
            await device_hub.detach_viewer(device_id, sid, transport)


# --- 「我的设备 / Agent」management (the owner manages the machines they enrolled) ---


class RenameDeviceRequest(BaseModel):
    name: str


class BindTeamRequest(BaseModel):
    team_id: int


async def _require_user(resolver: ActorResolverDep) -> int:
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError("Managing devices requires a logged-in user")
    return actor.user_id


def _device_screens(device_id: str) -> list[dict[str, Any]]:
    """The device's currently-open screens (agents), for the UI to open their 现场."""
    out: list[dict[str, Any]] = []
    for screen in device_hub.all_online_screens():
        if screen.device_id != device_id:
            continue
        out.append(
            {
                "sid": screen.sid,
                "agent_handle": screen.agent_handle,
                "agent_user_id": str(screen.agent_user_id),
                "project_id": str(screen.project_id) if screen.project_id else None,
                "topic_id": str(screen.topic_id) if screen.topic_id else None,
            }
        )
    return out


def _device_view(device: Device) -> dict[str, Any]:
    # A device is pure compute — no ``agent_handle`` here. The agents actually running
    # on it are the per-screen entries (each carries its own agent), surfaced below.
    return {
        "device_id": device.device_id,
        "name": device.name,
        "online": device_hub.is_online(device.device_id),
        "project_ids": [str(p) for p in device.project_ids],
        # Teams this machine is registered for (为团队注册设备): every project of
        # these teams may run on it.
        "team_ids": list(device.team_ids),
        "screens": _device_screens(device.device_id),
    }


@router.get("/my/devices")
async def my_devices(
    resolver: ActorResolverDep, service: DeviceServiceDep
) -> dict[str, Any]:
    """List the devices the logged-in human owns, with liveness + their open agents."""
    user_id = await _require_user(resolver)
    devices = await service.list_owned(user_id)
    return {"devices": [_device_view(d) for d in devices]}


@router.patch("/my/devices/{device_id}")
async def rename_my_device(
    device_id: str,
    body: RenameDeviceRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
) -> dict[str, Any]:
    """Rename a device the caller owns (the service enforces ownership)."""
    user_id = await _require_user(resolver)
    device = await service.rename_owned(device_id, body.name, actor_user_id=user_id)
    return _device_view(device)


@router.delete("/my/devices/{device_id}")
async def unbind_my_device(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep
) -> dict[str, Any]:
    """Unbind (forget) a device the caller owns — its durable token stops working."""
    user_id = await _require_user(resolver)
    await service.delete_owned(device_id, actor_user_id=user_id)
    return {"deleted": True, "device_id": device_id}


@router.post("/my/devices/{device_id}/teams")
async def register_device_for_team(
    device_id: str,
    body: BindTeamRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """为团队注册设备 (execution-architecture v4): bind a machine the caller owns to a
    team they belong to, so every project of that team can run on it. Owner-only, and
    the owner must be a member of the target team."""
    user_id = await _require_user(resolver)
    device = await service.get_device(device_id)
    if device is None or device.owner_user_id != user_id:
        raise NotFoundError("设备不存在或不属于你")
    if not await TeamRepository(db).is_team_member(body.team_id, user_id):
        raise ForbiddenError("你不是该团队成员，不能把设备注册给它")
    await service.assign_to_team(device_id, body.team_id, actor_user_id=user_id)
    device = await service.get_device(device_id)
    return _device_view(device)  # type: ignore[arg-type]


@router.get("/teams/{team_id}/devices")
async def team_devices(
    team_id: int,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """The machines registered for a team (为团队注册设备, v4) — the team's compute,
    with liveness. Any team member may view; every project of the team may run on
    these."""
    user_id = await _require_user(resolver)
    if not await TeamRepository(db).is_team_member(team_id, user_id):
        raise ForbiddenError("你不是该团队成员")
    devices = await service.list_devices_for_team(team_id)
    return {"devices": [_device_view(d) for d in devices]}


@router.delete("/my/devices/{device_id}/teams/{team_id}")
async def unregister_device_from_team(
    device_id: str,
    team_id: int,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
) -> dict[str, Any]:
    """Unbind a machine the caller owns from a team (为自己 / 换团队). Owner-only."""
    user_id = await _require_user(resolver)
    await service.unassign_from_team(device_id, team_id, actor_user_id=user_id)
    device = await service.get_device(device_id)
    if device is None:
        raise NotFoundError("设备不存在")
    return _device_view(device)
