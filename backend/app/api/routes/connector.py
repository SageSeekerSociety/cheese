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

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    BadRequestError,
    BaseError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.tokens import verify_session_token
from app.domain.agent.device_hub import (
    DeviceOffline,
    HubScreen,
    ViewerTransport,
    device_hub,
)
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply, Visibility
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.services import TaskService
from app.domain.team.repositories import TeamRepository
from app.domain.topic import transcripts
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_device_service(db: DbSession) -> DeviceService:
    """The per-request device service (SQL-backed by default). Exposed as an
    overridable dependency so a standalone spike / test can inject an in-memory repo
    while reusing these exact routes."""
    return DeviceService(SqlDeviceRepository(db))


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


async def recover_business_state(device_id: str) -> None:
    try:
        from app.api.deps import get_chat_service

        await get_chat_service().recover_sessions(device_id)
    except DeviceOffline:
        # It connected and went again before recovery could talk to it. The next
        # connection runs this, so there is nothing here to fix.
        logger.warning(
            "hook subscriptions not recovered: device %s went offline", device_id
        )
    except Exception:  # noqa: BLE001 — recovery cannot reject a healthy device
        logger.exception("hook subscription recovery failed for device %s", device_id)
    # Restore screen ownership before cleanup looks for sessions to close.
    from app.core.background import spawn
    from app.core.db import async_session_factory
    from app.domain.topic.retire import sweep_retired_storage

    spawn(
        sweep_retired_storage(async_session_factory),
        name="cleanup device reconnect",
    )
    try:
        # A Cloud topic whose machine just came up has been holding a message;
        # this attach is the last fact it was waiting for, so deliver now instead
        # of at the next sweep tick (machine/wakeup.py).
        from app.api.deps import get_cloud_wakeup

        await get_cloud_wakeup().wake_device(device_id)
    except Exception:  # noqa: BLE001 — a wake-up failure cannot reject the device
        logger.exception("cloud wake-up on attach failed for device %s", device_id)


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
        # Additive dual-read window: keep writing the old column's safe value, but
        # hosted access is now chosen per topic in device_topic.visibility.
        visibility=Visibility.isolated,
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
        try:
            await self._websocket.send_json(msg)
        except (WebSocketDisconnect, RuntimeError) as exc:
            # Starlette answers a write on a socket it already closed with a
            # RuntimeError, and a peer that dropped mid-write with a disconnect;
            # to the hub both mean the same thing: no link.
            raise ConnectionError(str(exc)) from exc


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
    await device_hub.attach_device(
        device.device_id, transport, name=device.name
    )  # sends welcome{v}

    recovery = (
        None
        if settings.device_connection_owner
        else asyncio.create_task(recover_business_state(device.device_id))
    )
    opened_at = time.monotonic()
    close_code: int | None = None
    try:
        while True:
            message = await websocket.receive_json()
            await device_hub.on_device_message(device.device_id, message)
    except WebSocketDisconnect as disconnect:
        close_code = disconnect.code
    finally:
        if recovery is not None:
            recovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await recovery
        # This link is meant to last the machine's whole uptime, and when it does
        # not, nothing anywhere said so: `except WebSocketDisconnect: pass`
        # discarded the close code, the only fact that names who hung up, and the
        # device's own cli has logged one line since it started. On dev the whole
        # fleet is replaced about once a minute — 71 closes and 71 accepts per
        # minute against 71 online devices — and that was invisible until the
        # alert noise around it was cleared (#1140).
        #
        # The code tells the halves apart: 1000/1001 is the peer closing on
        # purpose, 1006 is the connection dropping under it, and None means this
        # loop left by raising rather than by a disconnect at all. The age says
        # whether a link died young, which a count of closes cannot.
        logger.info(
            "device link closed device=%s code=%s after=%.1fs",
            device.device_id,
            close_code,
            time.monotonic() - opened_at,
        )
        await device_hub.detach_device(device.device_id, transport)


# --- transcripts: a device stores a home's raw session files here before the
# home is deleted (topic/retire.py, docs/where-a-turn-runs.md §8) --------------


async def _device_ran_place(
    service: DeviceService,
    device: Device,
    project_id: uuid.UUID,
    place_id: uuid.UUID,
) -> bool:
    """Whether this machine is the one whose home holds the place's transcripts.

    The pin says so directly while it stands. Once it is gone — the compute
    picker replaced it, or the place is not in the database any more — the
    machine has to at least be one the project may run on, directly or through
    its team, which is the same set `DeviceChannel` picks from."""
    binding = await service.topic_binding(place_id)
    if binding is not None:
        return binding.device_id == device.device_id
    return any(
        d.device_id == device.device_id
        for d in await service.list_devices_for_project(project_id)
    )


@router.put("/transcripts/{project_id}/{place_id}")
async def store_transcripts(
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    request: Request,
    service: DeviceServiceDep,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive one home's `.claude/projects` + `.claude/todos` as a tar.gz body.

    Authenticated with the durable device token as a bearer (the cli's own
    `CHEESE_TOKEN`), authorized by the pin. The archive is kept as a new
    timestamped file under `transcripts_dir/<project>/<place>/`, never
    replacing an earlier one; 413 past `transcripts_max_bytes`, 400 for a body
    that is not a whole archive, and neither keeps anything on disk."""
    scheme, _, token = (authorization or "").partition(" ")
    device = await service.verify_token(
        token.strip() if scheme.lower() == "bearer" else ""
    )
    if device is None:
        raise UnauthorizedError("unknown or missing device token")
    if await ProjectRepository(db).get(project_id) is None:
        raise NotFoundError("no such project")
    # The place may be a room or a thread, or already deleted; what it must not
    # be is a place of some other project wearing this project's path.
    place = await TopicRepository(db).get(place_id) or await TaskService(db).get(
        place_id
    )
    if place is not None and place.project_id != project_id:
        raise NotFoundError("no such place in this project")
    allowed = await _device_ran_place(service, device, project_id, place_id)
    placement = getattr(place, "session_placement", None)
    if placement and placement["device_id"] == device.device_id:
        allowed = True
    # Every read is done. Release the transaction before the body streams in:
    # an upload can take minutes, and a session held open across it would sit
    # `idle in transaction` on the topic tables for that long (#356).
    await db.commit()
    if not allowed:
        raise ForbiddenError("this machine did not run that place")
    try:
        stored = await transcripts.store(project_id, place_id, request.stream())
    except transcripts.ArchiveTooLarge as exc:
        raise BaseError(413, str(exc)) from exc
    except transcripts.NotAnArchive as exc:
        raise BadRequestError(str(exc)) from exc
    logger.info(
        "transcripts stored: project=%s place=%s device=%s file=%s size=%d sha256=%s",
        project_id,
        place_id,
        device.device_id,
        stored.path.name,
        stored.size,
        stored.sha256,
    )
    return {"file": stored.path.name, "size": stored.size, "sha256": stored.sha256}


# --- 现场 viewer: a browser watches a device screen's real terminal, and can type
# into it. Read-only is where this is GOING (see docs/where-a-turn-runs.md §6) --


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
    device = await service.get_hosted_device(device_id)
    if device is None or device.owner_user_id != user_id:
        raise NotFoundError("设备不存在或不属于你")
    if not await TeamRepository(db).is_team_member(body.team_id, user_id):
        raise ForbiddenError("你不是该团队成员，不能把设备注册给它")
    await service.assign_to_team(device_id, body.team_id, actor_user_id=user_id)
    device = await service.get_hosted_device(device_id)
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
    device = await service.get_hosted_device(device_id)
    if device is None:
        raise NotFoundError("设备不存在")
    return _device_view(device)
