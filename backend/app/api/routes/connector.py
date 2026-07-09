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
from app.domain.agent.device_hub import device_hub
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.identity.services import IdentityService

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
    approve_url = f"{base}/connector/connect?device_code={code}"
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
