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
process-global (``device_hub``).
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
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
from starlette.websockets import WebSocketState

from app.api.auth import ActorResolverDep
from app.common.auth import verify_access_token
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.sentences import say
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceOffline,
    HubScreen,
    ViewerTransport,
    device_hub,
)
from app.domain.agent.harness.claude_code import owner_login
from app.domain.device import owner_reads
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply
from app.domain.team.repositories import TeamRepository

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_device_service(db: DbSession) -> DeviceService:
    """The per-request device service (SQL-backed by default). Exposed as an
    overridable dependency so a standalone spike / test can inject an in-memory repo
    while reusing these exact routes."""
    return DeviceService(SqlDeviceRepository(db))


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


# How many machines are recovered at once. Recovery is per device and every
# device does it on connect, so a backend restart starts one per machine at the
# same instant — 71 on dev. Each walks that machine's sessions, and each session
# takes a database connection and then talks to the machine; unbounded, the
# burst wants far more connections than the pool has (20 + 15), and what it
# starves is every OTHER request, which is how a restart came out as minutes of
# 「QueuePool limit … connection timed out」 on page loads and background jobs
# (2026-09-19, and the same shape in the 09-18 flood). Nothing is dropped by
# waiting: a machine queued here is recovered a moment later, and its device
# link is already up.
_RECOVERY_AT_ONCE = 4
_recovering = asyncio.Semaphore(_RECOVERY_AT_ONCE)


async def recover_business_state(device_id: str) -> None:
    from app.core.background import spawn
    from app.core.db import async_session_factory
    from app.domain.local_fs.enforcement import push_grants_on_connect

    # The machine enforces its own copy of its directory grants, so it is sent
    # the current set as it connects, before anything else waits. It is not
    # behind the queue below: a revoke made while the machine was away is in
    # force on it only once this lands. And not behind the session-owner check:
    # every backend may send it, since the set is replaced whole on the machine.
    spawn(
        push_grants_on_connect(async_session_factory, device_hub, device_id),
        name="local grants device reconnect",
    )
    # Whether its owner's own Claude Code is logged in for the platform: only
    # the machine knows, and a session of it is placed by the answer.
    spawn(
        owner_login.refresh_on_connect(async_session_factory, device_hub, device_id),
        name="claude login device reconnect",
    )
    async with _recovering:
        await _recover_business_state(device_id)


async def _recover_business_state(device_id: str) -> None:
    from app.api.deps import get_chat_service, get_work_runner

    # Every backend watches devices come and go, but only the one running the
    # work listens to their sessions: two listeners land one session's output
    # twice. A backend still waiting to take over recovers every device when it
    # does; one on its way out leaves them to the next.
    if not get_work_runner().owns_sessions:
        return
    try:
        await get_chat_service().recover_sessions(device_id)
    except DeviceOffline:
        # It connected and went again before recovery could talk to it. The next
        # connection runs this, so there is nothing here to fix.
        logger.warning(
            "hook subscriptions not recovered: device %s went offline", device_id
        )
    except DeviceCallError as exc:
        # The machine answered with a failure of its own — a runner whose socket
        # is not up yet, a home that is gone. Its next connection runs this
        # again, so this is a state to wait out, not a fault to report: at ERROR
        # it was one alert per reconnect of a machine in that state (「dial unix
        # /tmp/cheese-execution-…sock: no such file」, 2026-09-19).
        logger.warning(
            "hook subscriptions not recovered: device %s said %s", device_id, exc
        )
    except Exception:  # noqa: BLE001 — recovery cannot reject a healthy device
        logger.exception("hook subscription recovery failed for device %s", device_id)
    # Restore screen ownership before cleanup looks for sessions to close.
    from app.core.background import spawn
    from app.core.db import async_session_factory
    from app.domain.machine.session_work import checkpoint_room
    from app.domain.topic.retire import sweep_retired_storage

    spawn(
        sweep_retired_storage(async_session_factory, checkpoint=checkpoint_room),
        name="cleanup device reconnect",
    )
    # A machine offline when a task of its rooms closed still has the checkout.
    from app.domain.room_task.checkouts import remove_closed_checkouts

    spawn(
        remove_closed_checkouts(async_session_factory, device_id=device_id),
        name="closed task checkouts device reconnect",
    )
    # Rooms from before rooms had an executor are told their files are kept
    # before anything archives them (`agent/device_storage.py`).
    from app.domain.agent.device_storage import keep_device_room_files

    spawn(
        keep_device_room_files(async_session_factory, device_id),
        name="kept room files device reconnect",
    )


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
    body: DeviceStartRequest, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any]:
    """Begin a device flow. The ``approve_url`` is built from the public base so a
    human can open it and approve this machine (fusion-design §5 device flow)."""
    code = await service.start(body.device_name)
    # The CLI can present this code to a human as soon as the response arrives.
    # Commit it now so a separate /connector/connect request can find it.
    await db.commit()
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
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError(say("deviceApproveSignIn"))

    if body.project_id is not None:
        await resolver.authorize_project(actor, project_id=body.project_id)

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


@router.get("/device/me")
async def device_me(
    db: DbSession,
    x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
) -> dict[str, Any]:
    """Which device a machine's stored token still names. A machine unbound
    since its approval keeps the token on disk, and `link connect` asks here
    before trusting it, rather than installing a connector the server will
    turn away on every dial."""
    device = await owner_reads.device_for_token(db, x_cheese_session or "")
    if device is None:
        raise UnauthorizedError("unknown or missing device token")
    return {"device_id": device.device_id, "name": device.name}


@router.websocket("/agent")
async def agent_socket(
    websocket: WebSocket,
    db: DbSession,
    x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
    token: str | None = Query(default=None),
) -> None:
    """The device's dial-out control channel. Authenticates with the durable device
    token (header, or ``?token=`` for browsers), then dispatches every inbound
    ``link.Msg`` to the shared ``DeviceHub`` (which drives outbound messages)."""
    device = await owner_reads.device_for_token(db, x_cheese_session or token or "")
    # End the auth read-transaction NOW, before the (device-lifetime) receive loop.
    # A ``Depends(get_db)`` session injected into a WebSocket route is only finalized
    # when the socket CLOSES — so without this commit, the authentication transaction
    # sits `idle in transaction` for the machine's entire uptime (observed: 2.8h),
    # holding an AccessShareLock on ``device_team`` that made an ALTER TABLE (ACCESS
    # EXCLUSIVE) on the device tables queue behind it until it timed out → site-wide
    # brownout (#356). Committing returns the connection to the pool (lock released)
    # for the life of the connection. The resolved identity is a plain dataclass,
    # so nothing lazy-loads after the commit.
    await db.commit()
    if device is None:
        # A token is necessary here (never sufficient; screen-scoped calls are
        # authorized per-actor). Reject with policy-violation.
        await websocket.close(code=1008, reason="unknown or missing device token")
        return
    # Before the handshake, so the write is done before the machine can hang up.
    await _note_seen(db, device.device_id)
    noted_at = time.monotonic()
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
            # A failed outbound send can disconnect an already accepted socket.
            if websocket.application_state is WebSocketState.DISCONNECTED:
                raise WebSocketDisconnect(code=1006)
            message = await asyncio.wait_for(
                websocket.receive_json(),
                device_hub.silence_allowed(device.device_id),
            )
            await device_hub.on_device_message(device.device_id, message)
            if time.monotonic() - noted_at >= SEEN_EVERY_S:
                await _note_seen(db, device.device_id)
                noted_at = time.monotonic()
    except WebSocketDisconnect as disconnect:
        close_code = disconnect.code
    except TimeoutError:
        # The machine stopped being heard from (`silence_allowed`). Leaving
        # here detaches it below, which is what fails its waiting calls and
        # takes it offline; the socket itself closes when the proxy lets go.
        logger.warning(
            "device link silent device=%s for %.0fs; taking it offline",
            device.device_id,
            device_hub.silence_allowed(device.device_id) or 0,
        )
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


#: How often a connected machine's last-seen time is written: often enough
#: for "last seen 3 hours ago", rarely enough to cost nothing.
SEEN_EVERY_S = 60


async def _note_seen(db: AsyncSession, device_id: str) -> None:
    """Keep that the machine is being heard from now: as it connects, then at
    most once every `SEEN_EVERY_S`. Not as its link goes — a link that goes
    with the server is torn down by cancelling this handler, and a database
    write cut short there leaves the connection broken for its next user.
    Committed at once, like the read that opened the link."""
    from app.domain.device.wiring import sql_device_service

    try:
        await sql_device_service(db).note_last_seen(device_id, datetime.now(UTC))
        await db.commit()
    except Exception:
        with contextlib.suppress(Exception):
            await db.rollback()
        logger.warning("last seen not kept for %s", device_id, exc_info=True)


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

    A screen working in a private channel, or in one of its tasks, is that
    channel's: only someone seated in it watches, project managers included —
    the same door as the channel's own routes (``authorize_topic``).
    """
    if not token:
        return False
    claims = verify_access_token(token)
    if claims is None:
        return False
    # Membership is keyed by handle.
    handle = claims.handle
    if screen.topic_id is not None:
        room = await owner_reads.room_of(session, screen.topic_id)
        if await owner_reads.members_only(session, room):
            return await owner_reads.topic_member(session, room, handle)
    if screen.project_id is not None:
        if await owner_reads.project_member(session, screen.project_id, handle):
            return True
        if await owner_reads.project_owner(session, screen.project_id) == handle:
            return True
    if screen.topic_id is not None:
        if await owner_reads.topic_member(session, screen.topic_id, handle):
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
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError(say("deviceManageSignIn"))
    return actor.user_id


async def _device_screens(db: AsyncSession, device_id: str) -> list[dict[str, Any]]:
    """The device's currently-open screens (agents), for the UI to open their 现场,
    each with the name its agent goes by."""
    from app.domain.machine.session_work import screen_agent_name

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
                **await screen_agent_name(
                    db, screen.project_id, screen.topic_id, screen.agent_handle
                ),
            }
        )
    return out


async def _device_view(db: AsyncSession, device: Device) -> dict[str, Any]:
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
        "last_seen_at": device.last_seen_at.isoformat()
        if device.last_seen_at
        else None,
        "screens": await _device_screens(db, device.device_id),
    }


@router.get("/my/devices")
async def my_devices(
    resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any]:
    """List the devices the logged-in human owns, with liveness + their open agents."""
    user_id = await _require_user(resolver)
    devices = await service.list_owned(user_id)
    views = []
    for device in devices:
        view = await _device_view(db, device)
        # Whether its owner's own Claude Code is logged in there for the
        # platform (#2991): the owner's to see, not the team's. Only a machine
        # they enrolled themselves runs it; a cloud machine says nothing.
        if device.supply == Supply.self_hosted:
            view["claude_code"] = await owner_login.status(db, device.device_id)
        # Its system, as its connector last said (`windows`, `darwin`,
        # `linux`), for the page to name a command that runs there; None
        # while it has not been heard from.
        view["system"] = device_hub.target(device.device_id).partition("-")[0] or None
        views.append(view)
    return {"devices": views}


@router.post("/my/devices/{device_id}/claude-code")
async def check_my_claude_code(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any] | None:
    """Ask one of the caller's machines again whether their own Claude Code is
    logged in there (#2991). They log in on the machine itself, which tells the
    server nothing; the page showing that machine asks, rather than leaving the
    answer from its last connection standing."""
    user_id = await _require_user(resolver)
    owned = {d.device_id: d for d in await service.list_owned(user_id)}
    device = owned.get(device_id)
    if device is None or device.supply != Supply.self_hosted:
        raise NotFoundError(say("deviceNotYours"))
    if device_hub.is_online(device_id):
        try:
            login = await owner_login.ask(device_hub, device_id)
        except Exception:
            # Gone or slow to answer: what it said last still stands.
            logger.warning("claude login not checked on %s", device_id, exc_info=True)
            login = None
        if login is not None:
            await owner_login.remember(db, device_id, login)
            await db.commit()
    return await owner_login.status(db, device_id)


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
        raise NotFoundError(say("deviceNotYours"))
    if not await TeamRepository(db).is_team_member(body.team_id, user_id):
        raise ForbiddenError(say("deviceTeamMemberOnly"))
    await service.assign_to_team(device_id, body.team_id, actor_user_id=user_id)
    device = await service.get_hosted_device(device_id)
    return await _device_view(db, device)  # type: ignore[arg-type]


@router.get("/teams/{team_id}/devices")
async def team_devices(
    team_id: int,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Every self-hosted machine the team's projects can run on, with liveness:
    the ones registered for the team, and the ones attached directly to one of
    its projects (``attached_projects`` names those projects). Any team member
    may view."""
    user_id = await _require_user(resolver)
    if not await TeamRepository(db).is_team_member(team_id, user_id):
        raise ForbiddenError(say("notTeamMember"))
    from app.domain.machine.session_work import device_users
    from app.domain.project.services import ProjectService

    names = {p.id: p.name for p in await ProjectService(db).list_for_team(team_id)}
    devices = await service.list_devices_for_team(team_id)
    listed = {d.device_id for d in devices}
    devices += [
        d
        for d in await service.list_devices_attached_to_projects(list(names))
        if d.device_id not in listed
    ]
    # Its owner sees who works on each of their machines, in any project (#1900
    # step 5). Nobody else does: a room's title is not every team member's.
    users = await device_users(
        db, [d.device_id for d in devices if d.owner_user_id == user_id]
    )
    return {
        "devices": [
            {
                **await _device_view(db, d),
                "attached_projects": [
                    {"id": str(p), "name": names[p]}
                    for p in d.project_ids
                    if p in names
                ],
                "in_use": users.get(d.device_id),
            }
            for d in devices
        ]
    }


@router.delete("/my/devices/{device_id}/teams/{team_id}")
async def unregister_device_from_team(
    device_id: str,
    team_id: int,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Unbind a machine the caller owns from a team (为自己 / 换团队). Owner-only."""
    user_id = await _require_user(resolver)
    await service.unassign_from_team(device_id, team_id, actor_user_id=user_id)
    device = await service.get_hosted_device(device_id)
    if device is None:
        raise NotFoundError(say("deviceNotFound"))
    return await _device_view(db, device)
