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
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.write_access import ROUTE_DECIDES
from app.auth.caller import handle_named_by
from app.common.auth import AccessClaims, verify_access_token
from app.core import background
from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.agent import checkout_sweep, model_connections
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceOffline,
    HubScreen,
    ViewerTransport,
    device_hub,
)
from app.domain.agent.device_storage import keep_device_room_files
from app.domain.agent.harness.claude_code import executor_launch, owner_login
from app.domain.agent_instance import account_connections
from app.domain.agent_instance.own import rename_for_connection
from app.domain.device import owner_reads, sandbox
from app.domain.device.login_kind import ConnectionKind
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.local_fs.enforcement import push_grants_on_connect
from app.domain.machine.session_work import (
    checkpoint_room,
    device_users,
    screen_agent_name,
)
from app.domain.project.services import ProjectService
from app.domain.team.repositories import TeamRepository
from app.domain.user.sessions import SessionService

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)


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
    # deferred-import: tests patch this name on app.core.db
    from app.core.db import async_session_factory

    # The machine enforces its own copy of its directory grants, so it is sent
    # the current set as it connects, before anything else waits. It is not
    # behind the queue below: a revoke made while the machine was away is in
    # force on it only once this lands. And not behind the session-owner check:
    # every backend may send it, since the set is replaced whole on the machine.
    background.spawn(
        push_grants_on_connect(async_session_factory, device_hub, device_id),
        name="local grants device reconnect",
    )
    # Whether its owner's own Claude Code is logged in for the platform: only
    # the machine knows, and a session of it is placed by the answer.
    background.spawn(
        owner_login.refresh_on_connect(async_session_factory, device_hub, device_id),
        name="claude login device reconnect",
    )
    # Whether it can give a session a sandbox: settled now, as it joins, rather
    # than first found out when a room runs a command there.
    background.spawn(
        _check_sandbox(async_session_factory, device_id),
        name="sandbox check device reconnect",
    )
    async with _recovering:
        await _recover_business_state(device_id)


async def _check_sandbox(session_factory, device_id: str) -> None:
    """Ask a machine as it connects whether it can give a session a sandbox,
    and keep the answer. One that goes again before answering is asked on its
    next connection."""
    try:
        async with session_factory() as session:
            service = sql_device_service(session)
            device = await service.get_device(device_id)
            if device is None:
                return
            await sandbox.refresh(
                service, device_hub, device, executor_launch.sandbox_check_script()
            )
            await session.commit()
    except Exception:
        logger.warning("sandbox not checked on %s", device_id, exc_info=True)


async def _recover_business_state(device_id: str) -> None:
    # deferred-import: tests patch this name on app.api.deps
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
    # deferred-import: tests patch this name on app.core.db
    from app.core.db import async_session_factory

    # deferred-import: tests patch this name on app.domain.room_cleanup.retire
    from app.domain.room_cleanup.retire import sweep_retired_storage

    background.spawn(
        sweep_retired_storage(async_session_factory, checkpoint=checkpoint_room),
        name="cleanup device reconnect",
    )
    # A machine offline when a task of its rooms closed still has the checkout.
    # deferred-import: tests patch this name on app.domain.room_task.checkouts
    from app.domain.room_task.checkouts import remove_closed_checkouts

    background.spawn(
        remove_closed_checkouts(
            async_session_factory, device_id=device_id, machines=checkout_sweep.sweep
        ),
        name="closed task checkouts device reconnect",
    )
    # Rooms from before rooms had an executor are told their files are kept
    # before anything archives them (`agent/device_storage.py`).

    background.spawn(
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


async def _sign_in_lasts(session: AsyncSession, claims: AccessClaims) -> bool:
    """Whether the sign-in the token names may still act at all.

    ``verify_access_token`` reads the signature and the expiry and nothing
    else, so a token outlives its session -- deliberately, for the length of a
    request. A connection is the caller that outlives it by far more: signed
    out, revoked from the device list, password changed or reset, idle past
    the timeout, or theft suspected, the session ends and the token keeps
    working until it expires. A token that names no session has none to ask
    about, and one that names no person has no session to look up.
    """
    if claims.sid is None or claims.user_id is None:
        return True
    sign_ins = SessionService(session)
    return await sign_ins.live_handle(claims.user_id, claims.sid) is not None


async def _may_view_screen(
    session: AsyncSession, screen: HubScreen, claims: AccessClaims | None
) -> bool:
    """Only a logged-in human who shares the screen's project (a project member/owner)
    or its topic (a topic-roster member) may watch its 现场 (P1 authz shape). Browsers
    can't set an Authorization header on a WS, so the token rides as ``?token=``.

    A screen working in a private channel, or in one of its tasks, is that
    channel's: only someone seated in it watches, project managers included —
    the same door as the channel's own routes (``authorize_topic``).

    Asked at the handshake and again on the viewer's clock, which is why the
    caller holds the claims rather than the token: what a lasting view stands
    on is the sign-in, not the 15-minute access token it was opened with.
    """
    if claims is None or not await _sign_in_lasts(session, claims):
        return False
    # Membership is keyed by handle, and the claims are read the same way the
    # workspace routes read them: an all-digit one is a NAME, not a user id.
    handle = await handle_named_by(session, claims)
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


#: How often a live viewer is asked again whether it may still watch and type:
#: often enough that a sign-in which ended stops driving the machine, rarely
#: enough to cost nothing while a screen is only being watched.
VIEW_RECHECK_EVERY_S = 25.0


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

    The door is asked again every ``VIEW_RECHECK_EVERY_S`` while the view lasts:
    the handshake is not the last word, so a view ends with the sign-in and the
    membership that opened it rather than at the next reconnect.

    Unknown-screen and not-authorized close identically (1008) so a screen id
    can't be enumerated."""
    screen = device_hub.screen(sid)
    claims = verify_access_token(token or "")
    authorized = (
        screen is not None
        and claims is not None
        and await _may_view_screen(db, screen, claims)
    )
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
    incoming: asyncio.Task[Any] | None = None  # a receive must not be cancelled
    # The review is a clock of its own, never a per-keystroke errand: the viewer
    # typing steadily is exactly the one whose sign-in must not lapse unnoticed,
    # so a busy view is asked as often as an idle one. The tick is made once a
    # period and is not remade when a frame arrives.
    review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
    try:
        while True:
            if incoming is None:
                incoming = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait(
                {incoming, review}, return_when=asyncio.FIRST_COMPLETED
            )
            if review in done:
                review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
                if not await _may_view_screen(db, screen, claims):
                    await websocket.close(code=4401, reason="this sign-in has ended")
                    break
                # The same footgun as the check before the handshake: a read left
                # open parks this socket's session `idle in transaction`.
                await db.commit()
                continue
            message = incoming.result()
            incoming = None
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
        review.cancel()
        if incoming is not None:
            incoming.cancel()
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


async def _my_self_hosted(
    device_id: str, resolver: ActorResolverDep, service: DeviceService
) -> None:
    """Only the machine's owner, and only for a machine they enrolled."""
    user_id = await _require_user(resolver)
    owned = {d.device_id: d for d in await service.list_owned(user_id)}
    device = owned.get(device_id)
    if device is None or device.supply != Supply.self_hosted:
        raise NotFoundError(say("deviceNotYours"))


class ClaudeCodeLoginOut(BaseModel):
    """What a machine last said about its owner's Claude Code login."""

    installed: bool
    logged_in: bool
    #: `claude.ai` for a subscription, the API key kind, or `model_service`.
    auth_method: str | None
    subscription_type: str | None
    model: str | None
    service_host: str | None
    #: What its owner named the connection; None until they do.
    name: str | None
    checked_at: str
    #: Removed while the machine was away; it logs out as it next connects.
    revoking: bool


def _login_out(login: dict | None) -> ClaudeCodeLoginOut | None:
    return None if login is None else ClaudeCodeLoginOut.model_validate(login)


class ModelConnectionOut(ClaudeCodeLoginOut):
    device_id: str
    device_name: str
    online: bool
    #: When a session of the owner's Claude Code last ran on that machine.
    last_used_at: str | None


class AccountConnectionOut(BaseModel):
    """A model connection the caller's account holds: an Anthropic API key or
    a model service, kept sealed by the platform. Never the key."""

    id: str
    kind: ConnectionKind
    #: What its owner calls it.
    name: str | None
    #: The key's last four characters, to tell keys apart.
    key_last4: str
    #: A model service's host and model; None for an API key.
    service_host: str | None
    model: str | None
    #: When the service last answered a check, and its refusal if it refused.
    checked_at: str | None
    check_error: str | None
    created_at: str


class ModelConnectionsOut(BaseModel):
    #: The logins the caller's machines hold.
    connections: list[ModelConnectionOut]
    #: The connections the caller's account holds.
    account_connections: list[AccountConnectionOut]


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


class AccountConnectionIn(BaseModel):
    """An API key or a model service to keep on the caller's account."""

    kind: Literal[ConnectionKind.api_key, ConnectionKind.model_service]
    key: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ]
    #: A model service's address and model; an API key calls Anthropic.
    base_url: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, pattern=r"^https://[^\s/]+\S*$", max_length=512
            ),
        ]
        | None
    ) = None
    model: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, pattern=r"^\S+$", max_length=128),
        ]
        | None
    ) = None
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None
    ) = None


class ConnectionRename(BaseModel):
    """What its owner calls a model connection; empty takes the name away."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


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


@router.post("/my/devices/{device_id}/claude-code")
async def check_my_claude_code(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any] | None:
    """Ask one of the caller's machines again whether their own Claude Code is
    logged in there (#2991). They log in on the machine itself, which tells the
    server nothing; the page showing that machine asks, rather than leaving the
    answer from its last connection standing. A logout its owner asked for
    while it was away is carried out first."""
    await _my_self_hosted(device_id, resolver, service)
    if device_hub.is_online(device_id):
        try:
            await owner_login.settle(db, device_hub, device_id)
            login = await owner_login.ask(device_hub, device_id)
        except Exception:
            # Gone or slow to answer: what it said last still stands.
            logger.warning("claude login not checked on %s", device_id, exc_info=True)
            login = None
        if login is not None:
            await owner_login.remember(db, device_id, login)
            await db.commit()
    return await owner_login.status(db, device_id)


# The handler decides: only the machine's owner, a person, may remove it.
@router.delete("/my/devices/{device_id}/claude-code", dependencies=[ROUTE_DECIDES])
async def revoke_my_claude_code(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> ClaudeCodeLoginOut | None:
    """Log the caller's own Claude Code out on one of their machines, from any
    browser (#2991). Out of use at once; the machine logs out now when online,
    else as it next connects."""
    await _my_self_hosted(device_id, resolver, service)
    await owner_login.revoke(db, device_hub, device_id)
    return _login_out(await owner_login.status(db, device_id))


# The handler decides: only the machine's owner, a person, may name it.
@router.patch("/my/devices/{device_id}/claude-code", dependencies=[ROUTE_DECIDES])
async def rename_my_claude_code(
    device_id: str,
    body: ConnectionRename,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> ClaudeCodeLoginOut:
    """Name the login the caller's own Claude Code has on one of their machines
    (#2991); the own agents on it are named after it, whatever the machine
    reports later. The machine need not be online."""
    await _my_self_hosted(device_id, resolver, service)
    if not await owner_login.rename(db, device_id, body.name):
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await db.commit()
    return ClaudeCodeLoginOut.model_validate(await owner_login.status(db, device_id))


class ModelServiceRequest(BaseModel):
    base_url: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^https?://[^\s/]+")
    ]
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    model: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\S+$")]


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
    """Point the caller's own Claude Code on one of their machines at another
    model service, from any browser (#2991). The key is handed to the machine,
    which keeps it; it is not stored here. The machine has to be online."""
    await _my_self_hosted(device_id, resolver, service)
    if not device_hub.is_online(device_id):
        raise ConflictError(say("modelServiceMachineOffline"))
    try:
        await owner_login.use_model_service(
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
    return _login_out(await owner_login.status(db, device_id))


# The handler decides: only the machine's owner, a person, may ask it.
@router.post("/my/devices/{device_id}/sandbox", dependencies=[ROUTE_DECIDES])
async def check_my_sandbox(
    device_id: str, resolver: ActorResolverDep, service: DeviceServiceDep, db: DbSession
) -> dict[str, Any] | None:
    """Ask one of the caller's machines again whether it can give a session a
    sandbox: after its owner changed what it lacked, the page asks rather than
    leaving the answer from its last connection standing."""
    user_id = await _require_user(resolver)
    owned = {d.device_id: d for d in await service.list_owned(user_id)}
    device = owned.get(device_id)
    if device is None or device.supply != Supply.self_hosted:
        raise NotFoundError(say("deviceNotYours"))
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
