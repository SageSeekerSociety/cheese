"""A person's terminal in a task: one browser tab on one of their shells.

``WS /connector/tasks/{task}/terminals/{id}?token=&cols=&rows=`` attaches the
caller to their shell ``id`` in the task's environment, opening it first when
they have none by that id (`machine.terminals`, `runtime_terminal`). Served
by the device connection's owner, like the 现场 viewer: that process holds
the machine's link and outlives the business backend's releases, so a
release does not cut a person's terminal.

Down, BINARY frames are what the shell wrote and TEXT frames say what
happened, as JSON: ``attached`` (what follows starts with what it last
wrote; ``ended`` when it has exited already), ``exit`` (the shell ended),
``refused`` (no shell: ``not_owner``, ``unsupported``, ``outdated``,
``limit``, ``unavailable``). Up, BINARY frames are keystrokes and TEXT frames
are ``resize`` (``cols``, ``rows``) and ``ack`` (``bytes`` of one BINARY
frame written to the screen: output is passed on as the browser takes it, so
a flood waits on the machine).

The door is the one the task's files have — someone who may edit the task,
signed in — plus the computer's: on someone's own computer only its owner.
It is asked at the handshake and again every ``VIEW_RECHECK_EVERY_S``. A
person typing, or watching a program run in the shell's foreground, is
using the task's sandbox, which then is not taken away for sitting idle.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.api.request_deps import DbSession
from app.api.routes.connector.screen import (
    VIEW_RECHECK_EVERY_S,
    _as_int,
    _sign_in_lasts,
)
from app.api.write_access import Can, InChannel
from app.common.auth import AccessClaims, verify_access_token
from app.core.config import settings
from app.domain.agent.device_hub import DeviceOffline, device_hub
from app.domain.agent.device_hub.streams import HubStream, StreamsUnsupported
from app.domain.machine import preview_wake, terminals
from app.domain.project_permissions import Permission
from app.domain.room_task.place import TaskStatus
from app.domain.user.services import user_by_handle

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)

_EDITS = Can(Permission.edit_content, at="topic_id").dependency
#: The frames the executor writes on a shell's stream (`runtime_terminal`):
#: a type byte and a four-byte length.
_HEAD = 5
_OUTPUT, _STATUS, _EXIT = ord("o"), ord("s"), ord("x")
#: How often a keystroke moves the sandbox's idle clock, at most.
WAKE_EVERY_S = 60.0


class _Person:
    """Who is at the terminal, as the door last found them."""

    def __init__(self, task_id: uuid.UUID, user_id: int, handle: str, workspace: bool):
        self.task_id, self.user_id = task_id, user_id
        self.handle, self.in_workspace = handle, workspace


async def _door(
    db: AsyncSession, task_id: uuid.UUID, token: str, claims: AccessClaims | None
) -> _Person | None:
    """The person, when they may open a terminal in the task now: signed
    in, a person rather than an AI teammate, allowed to edit the task, and
    the task still open. None otherwise."""
    if claims is None or not await _sign_in_lasts(db, claims):
        return None
    resolver = ActorResolver(session=db, bearer=token, cheese_token="")
    try:
        at: InChannel = await _EDITS(topic_id=task_id, db=db, resolver=resolver)
    except Exception:  # refused, or nothing there: the same closed door
        return None
    task = at.place.task
    if (
        task is None
        or task.status != TaskStatus.open
        or not at.actor.authenticated
        or at.actor.via == "cheese"
    ):
        return None
    user_id = at.actor.user_id
    if user_id is None:
        user = await user_by_handle(db, at.actor.handle)
        if user is None:
            return None
        user_id = user.id
    return _Person(task.id, user_id, at.actor.handle, not task.branch_name)


class _Relay:
    """One browser tab on one shell's stream."""

    def __init__(self, websocket: WebSocket, stream: HubStream) -> None:
        self.websocket, self.stream = websocket, stream
        self.buffer = bytearray()
        #: Frame sizes of the output sent to the browser and not yet acked.
        self.unacked: collections.deque[int] = collections.deque()
        self.busy = False
        self.refused: str | None = None

    async def from_machine(self) -> None:
        """Pass on what the shell wrote until the stream ends."""
        while (chunk := await self.stream.output.get()) is not None:
            self.buffer.extend(chunk)
            if self.buffer[:1] == b"{":
                # An executor older than shells answered the request as a call.
                answer = bytes(self.buffer).split(b"\n", 1)[0]
                self.refused = (
                    terminals.Refusal.outdated
                    if terminals.UNKNOWN_METHOD.encode() in answer
                    else "unavailable"
                )
                return
            while len(self.buffer) >= _HEAD:
                kind = self.buffer[0]
                length = int.from_bytes(self.buffer[1:_HEAD], "big")
                if len(self.buffer) < _HEAD + length:
                    break
                payload = bytes(self.buffer[_HEAD : _HEAD + length])
                del self.buffer[: _HEAD + length]
                await self._frame(kind, payload)

    async def _frame(self, kind: int, payload: bytes) -> None:
        if kind == _OUTPUT:
            self.unacked.append(_HEAD + len(payload))
            await self.websocket.send_bytes(payload)
            return
        await self.stream.ack(_HEAD + len(payload))
        if kind == _EXIT:
            code = _as_int(payload.decode(errors="replace"), 0)
            await self.websocket.send_text(json.dumps({"type": "exit", "code": code}))
            return
        if kind != _STATUS:
            return
        try:
            status = json.loads(payload)
        except ValueError:
            return
        if not isinstance(status, dict):
            return
        if "busy" in status:
            self.busy = bool(status["busy"])
        if "attached" in status:
            await self.websocket.send_text(
                json.dumps({"type": "attached", "ended": bool(status.get("ended"))})
            )
        elif "refused" in status:
            reason = status["refused"]
            self.refused = (
                reason
                if reason in (terminals.Refusal.limit, terminals.Refusal.unsupported)
                else "unavailable"
            )

    async def acked(self) -> None:
        """The browser wrote one output frame to its screen."""
        if self.unacked:
            await self.stream.ack(self.unacked.popleft())


def _frame(kind: bytes, payload: bytes) -> bytes:
    return kind + len(payload).to_bytes(4, "big") + payload


async def _refuse(websocket: WebSocket, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_text(json.dumps({"type": "refused", "reason": reason}))
        await websocket.close(code=1000)


@router.websocket("/tasks/{task_id}/terminals/{terminal_id}")
async def terminal_socket(
    websocket: WebSocket,
    task_id: uuid.UUID,
    terminal_id: str,
    db: DbSession,
    token: str | None = Query(default=None),
    cols: int = Query(default=80),
    rows: int = Query(default=24),
) -> None:
    """Relay one browser tab to one of the caller's shells in the task.

    Whoever passes the door can run anything in the task's environment as
    its agent can: one environment is one trust scope, and the person's
    shell gets nothing the agent there does not have — no credential of the
    person's own, so nothing only a person may do (`machine.terminals`).

    Not allowed and nothing there close identically (1008), so a task id
    cannot be probed."""
    claims = verify_access_token(token or "")
    person = await _door(db, task_id, token or "", claims)
    refused = shells = None
    if person is not None:
        refused = await terminals.refusal(
            db, person.task_id, person.user_id, hub=device_hub
        )
        shells = await terminals.shells_of(
            db,
            person.task_id,
            user_id=person.user_id,
            handle=person.handle,
            in_workspace=person.in_workspace,
        )
    # The door's reads end before the long relay: a session left in a
    # transaction for the life of the socket would hold its locks as long.
    await db.commit()
    if person is None:
        await websocket.close(code=1008, reason="cannot open this terminal")
        return
    await websocket.accept()
    if refused is not None:
        await _refuse(websocket, refused)
        return
    if shells is None:
        # No machine holds the task's copy for this person (yet): the page
        # brings one up (`machine/prepare`) and comes back.
        await _refuse(websocket, "unavailable")
        return
    bind = db.bind
    await preview_wake.wake_for_files(bind, person.task_id, user_id=person.user_id)
    header = json.dumps(
        {
            "method": "terminal",
            "params": {
                **shells.params,
                "id": terminal_id,
                "cols": _as_int(cols, 80),
                "rows": _as_int(rows, 24),
            },
        }
    )
    try:
        stream = await device_hub.open_executor_stream(
            shells.device_id, shells.state, header
        )
    except StreamsUnsupported:
        await _refuse(websocket, terminals.Refusal.outdated)
        return
    except DeviceOffline:
        await _refuse(websocket, "unavailable")
        return
    relay = _Relay(websocket, stream)
    machine = asyncio.create_task(relay.from_machine())
    incoming: asyncio.Task[Any] | None = None
    review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
    typed_at = woke_at = time.monotonic()
    cap = float(settings.cloud_sandbox_background_cap_s)
    try:
        while True:
            if incoming is None:
                incoming = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait(
                {incoming, review, machine}, return_when=asyncio.FIRST_COMPLETED
            )
            if machine in done:
                if relay.refused is not None:
                    await _refuse(websocket, relay.refused)
                else:
                    with contextlib.suppress(Exception):
                        await websocket.close(code=1000)
                break
            if review in done:
                review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
                if await _door(db, task_id, token or "", claims) is None:
                    await db.commit()
                    with contextlib.suppress(Exception):
                        await websocket.close(
                            code=4401, reason="this sign-in has ended"
                        )
                    break
                await db.commit()
                # Watching a program run in the shell is using the sandbox,
                # for as long as a background command of the agent's would.
                now = time.monotonic()
                if relay.busy and now - typed_at < cap:
                    woke_at = now
                    await preview_wake.wake_for_files(
                        bind, person.task_id, user_id=person.user_id
                    )
                continue
            message = incoming.result()
            incoming = None
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if data:
                    await stream.send(_frame(b"i", data))
                    typed_at = time.monotonic()
                    if typed_at - woke_at >= WAKE_EVERY_S:
                        woke_at = typed_at
                        await preview_wake.wake_for_files(
                            bind, person.task_id, user_id=person.user_id
                        )
                continue
            try:
                control = json.loads(message.get("text") or "")
            except ValueError:
                continue
            if not isinstance(control, dict):
                continue
            if control.get("type") == "ack":
                await relay.acked()
            elif control.get("type") == "resize":
                size = {
                    "cols": _as_int(control.get("cols"), 80),
                    "rows": _as_int(control.get("rows"), 24),
                }
                await stream.send(_frame(b"r", json.dumps(size).encode()))
    except (WebSocketDisconnect, DeviceOffline):
        pass
    finally:
        review.cancel()
        machine.cancel()
        if incoming is not None:
            incoming.cancel()
        await stream.close()
