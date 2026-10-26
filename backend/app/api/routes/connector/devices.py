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

原来单文件 ``app/api/routes/connector.py`` 按职责切开后的设备那一半：设备流、设备
控制通道、以及机器重连时的业务恢复。门面在 ``app/api/routes/connector/__init__.py``，
按原来的声明顺序把这些 router 挂上去。
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.routes.connector.schemas import (
    ConnectRequest,
    DevicePollRequest,
    DeviceStartRequest,
)
from app.api.write_access import ROUTE_DECIDES
from app.core import background
from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.sentences import say
from app.domain.agent import checkout_sweep
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceOffline,
    device_hub,
)
from app.domain.agent.device_storage import keep_device_room_files
from app.domain.agent.harness.claude_code import executor_launch, owner_login
from app.domain.device import owner_reads, sandbox
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.local_fs.enforcement import push_grants_on_connect
from app.domain.machine.session_work import checkpoint_room
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.services import team_service

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
    except Exception:  # recovery cannot reject a healthy device
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


# --- device flow ---------------------------------------------------------------


@router.post("/auth/device/start", dependencies=[ROUTE_DECIDES])
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


@router.post("/auth/device/poll", dependencies=[ROUTE_DECIDES])
async def device_poll(
    body: DevicePollRequest,
    service: DeviceServiceDep,
    db: DbSession,
    response: Response,
) -> dict[str, Any]:
    """Poll a pending flow: ``{status}`` until approved, then the durable token +
    device id the client persists. The token's digest is committed before the
    answer leaves: the cli dials in with it at once. No cache may keep the answer
    (RFC 6749 §5.1)."""
    polled = await service.poll(body.device_code)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return polled


@router.get("/auth/device/proposed-name")
async def device_proposed_name(
    code: str, service: DeviceServiceDep
) -> dict[str, str | None]:
    """The name the cli proposed for a pending ``code`` (this machine's hostname) so
    the approval page can prefill it — editable. Behind the code (the approval secret),
    same trust level as start/poll; ``null`` when the code is unknown/expired."""
    return {"device_name": await service.code_device_name(code)}


@router.post("/connect", dependencies=[ROUTE_DECIDES])
async def device_connect(
    body: ConnectRequest,
    resolver: ActorResolverDep,
    service: DeviceServiceDep,
    db: DbSession,
) -> dict[str, Any]:
    """Enroll a device in the selected team under its administrator’s authority."""
    actor = await resolver.resolve()
    if not actor.authenticated or actor.user_id is None:
        raise UnauthorizedError(say("deviceApproveSignIn"))

    team_id = body.team_id
    if team_id is None:
        team_id = (await team_service(db).ensure_personal_team(actor.user_id)).id
    if not await team_seats.may(
        db, team_id, actor.user_id, TeamPermission.manage_machines
    ):
        raise ForbiddenError(say("deviceTeamAdminOnly"))

    # Approve binds the device to its owner + mints the durable token. The device is
    # PURE COMPUTE (execution-architecture v3: a ComputePool node) — enrolling a machine
    # does NOT mint an agent. The agent a screen runs as is resolved per project/topic
    # at turn time (fusion-design §5: agent = screen), independent of the host.
    device = await service.approve(
        body.device_code,
        owner_user_id=actor.user_id,
        team_id=team_id,
        # 入口决定待遇 (#282 决定 2): a human ran the connector on a machine they
        # already keep running, so the platform may never stop or destroy it —
        # only stop using it. A CONSTANT here, the mirror of the MicroCloud
        # enrolment sweep's `Supply.cloud`.
        supply=Supply.self_hosted,
        name=body.device_name,
    )
    return {
        "device_id": device.device_id,
        "device_name": device.name,
        "team_id": device.team_id,
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
    # holding an AccessShareLock on ``device`` that makes an ALTER TABLE (ACCESS
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
