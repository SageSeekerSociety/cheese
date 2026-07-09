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
from app.core.errors import UnauthorizedError
from app.core.tokens import verify_session_token
from app.domain.agent.device_hub import HubScreen, ViewerTransport, device_hub
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.identity.services import IdentityService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.user.models import User

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
    project_id: uuid.UUID | None = None


# --- device flow ---------------------------------------------------------------


@router.post("/auth/device/start")
async def device_start(
    body: DeviceStartRequest, service: DeviceServiceDep
) -> dict[str, Any]:
    """Begin a device flow. The ``approve_url`` is built from the public base so a
    human can open it and approve this machine (fusion-design §5 device flow)."""
    code = await service.start(body.device_name)
    base = settings.connector_public_base.rstrip("/")
    # The approve link points a human at the frontend ``/connect`` approval page: there,
    # behind login, they bind this machine to a project and mint its agent (item 3).
    approve_url = f"{base}/connect?code={code}"
    return {"device_code": code, "approve_url": approve_url, "interval": 2}


@router.post("/auth/device/poll")
async def device_poll(
    body: DevicePollRequest, service: DeviceServiceDep
) -> dict[str, Any]:
    """Poll a pending flow: ``{status}`` until approved, then the durable token +
    device id the client persists."""
    return await service.poll(body.device_code)


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

    # The pending code carries the human-proposed device name; mint the agent-user
    # for it, then approve (binding owner + agent + minting the durable token).
    identity = IdentityService(db)
    device_name = await service.code_device_name(body.device_code) or "device"
    agent_user = await identity.create_device_agent(name=f"{device_name} · agent")
    device = await service.approve(
        body.device_code,
        owner_user_id=actor.user_id,
        agent_user_id=agent_user.id,
    )
    if body.project_id is not None:
        await service.assign_to_project(
            device.device_id, body.project_id, actor_user_id=actor.user_id
        )
    return {
        "device_id": device.device_id,
        "device_name": device.name,
        "agent_handle": agent_user.handle,
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
    x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
    token: str | None = Query(default=None),
) -> None:
    """The device's dial-out control channel. Authenticates with the durable device
    token (header, or ``?token=`` for browsers), then dispatches every inbound
    ``link.Msg`` to the shared ``DeviceHub`` (which drives outbound messages)."""
    device = await service.verify_token(x_cheese_session or token or "")
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
    handle = claims["sub"]
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
    """Relay one browser terminal ← one device screen, byte-for-byte. Read-only: the
    viewer receives raw ``screen.data`` (fanned out by the hub) and may only send
    ``resize`` control frames (JSON text) — keystrokes are ignored. Unknown-screen and
    not-authorized close identically (1008) so a screen id can't be enumerated."""
    screen = device_hub.screen(sid)
    if screen is None or not await _may_view_screen(db, screen, token):
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
                continue  # read-only: ignore any keystroke bytes
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


async def _require_user(resolver: ActorResolverDep) -> uuid.UUID:
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


async def _device_view(db: AsyncSession, device: Device) -> dict[str, Any]:
    agent = await db.get(User, device.agent_user_id)
    return {
        "device_id": device.device_id,
        "name": device.name,
        "online": device_hub.is_online(device.device_id),
        "agent_handle": agent.handle if agent is not None else None,
        "project_ids": [str(p) for p in device.project_ids],
        "screens": _device_screens(device.device_id),
    }


@router.get("/my/devices")
async def my_devices(
    resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any]:
    """List the devices the logged-in human owns, with liveness + their open agents."""
    user_id = await _require_user(resolver)
    devices = await service.list_owned(user_id)
    return {"devices": [await _device_view(db, d) for d in devices]}


@router.patch("/my/devices/{device_id}")
async def rename_my_device(
    device_id: str,
    body: RenameDeviceRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Rename a device the caller owns (the service enforces ownership)."""
    user_id = await _require_user(resolver)
    device = await service.rename_owned(device_id, body.name, actor_user_id=user_id)
    return await _device_view(db, device)


@router.delete("/my/devices/{device_id}")
async def unbind_my_device(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep
) -> dict[str, Any]:
    """Unbind (forget) a device the caller owns — its durable token stops working."""
    user_id = await _require_user(resolver)
    await service.delete_owned(device_id, actor_user_id=user_id)
    return {"deleted": True, "device_id": device_id}
