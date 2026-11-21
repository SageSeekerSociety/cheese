"""A language server beside a task's working copy, for one browser tab.

``WS /connector/tasks/{task}/languages/{language}?token=`` starts a language
server for ``language`` (``c``, ``cpp``, …) in the task's checkout on the
machine that holds it (`machine.languages`, `runtime_language`) and relays its
stdio. Served by the device connection's owner, as the terminal is.

The first frame is TEXT JSON: ``ready`` (``root``: the checkout's absolute
path on the machine) or ``refused`` (``reason``: ``not_owner``,
``unsupported``, ``outdated``, ``limit``, ``unavailable``) and a close. After
``ready``, BINARY frames both ways are the server's stdin and stdout verbatim
(the LSP base protocol; a frame's boundaries mean nothing). The socket
closing ends the server; the server ending closes the socket (1000).

The door is the terminal's — someone who may edit the task, signed in, and on
someone's own computer only its owner — asked at the handshake and again
every ``VIEW_RECHECK_EVERY_S``. Opening one never brings a machine up and is
not using it: no machine holding the task's copy awake is ``unavailable``,
and nothing here moves the sandbox's idle clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.request_deps import DbSession
from app.api.routes.connector.screen import VIEW_RECHECK_EVERY_S
from app.api.routes.connector.terminal import _door, _refuse
from app.common.auth import verify_access_token
from app.domain.agent.device_hub import DeviceOffline, device_hub
from app.domain.agent.device_hub.streams import HubStream, StreamsUnsupported
from app.domain.machine import languages, terminals

router = APIRouter(prefix="/connector", tags=["connector"])

#: What the executor answers before the server's bytes: one JSON line, at
#: most this long.
_ANSWER_MAX = 64 * 1024
_REFUSALS = {terminals.Refusal.limit.value, terminals.Refusal.unsupported.value}
#: How long the executor has to answer before the page is told there is none.
ANSWER_WITHIN_S = 30.0


async def _answer(stream: HubStream) -> tuple[dict | None, bytes]:
    """The executor's answer line, and whatever of the server's output came
    with it; None when the stream ended or overran before a whole line."""
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = await stream.output.get()
        if chunk is None or len(buffer) + len(chunk) > _ANSWER_MAX:
            return None, b""
        buffer.extend(chunk)
    line, rest = bytes(buffer).split(b"\n", 1)
    await stream.ack(len(line) + 1)
    try:
        answer = json.loads(line)
    except ValueError:
        return None, b""
    return (answer if isinstance(answer, dict) else None), rest


def _refusal(answer: dict | None) -> str | None:
    """What the page is told instead of ``ready``, or None for a server."""
    if answer is None:
        return "unavailable"
    error = answer.get("error")
    if error is None:
        result = answer.get("result")
        found = isinstance(result, dict) and result.get("root")
        return None if found else "unavailable"
    if terminals.UNKNOWN_METHOD in str(error):
        return terminals.Refusal.outdated
    return str(error) if error in _REFUSALS else "unavailable"


async def _pass_up(websocket: WebSocket, stream: HubStream, first: bytes) -> None:
    """The server's output to the page, acked as the page takes it."""
    chunk: bytes | None = first
    while chunk is not None:
        if chunk:
            await websocket.send_bytes(chunk)
            await stream.ack(len(chunk))
        chunk = await stream.output.get()


@router.websocket("/tasks/{task_id}/languages/{language}")
async def language_socket(
    websocket: WebSocket,
    task_id: uuid.UUID,
    language: str,
    db: DbSession,
    token: str | None = Query(default=None),
) -> None:
    """Relay one browser tab to one language server in the task's checkout.

    Not allowed and nothing there close identically (1008), so a task id
    cannot be probed."""
    claims = verify_access_token(token or "")
    person = await _door(db, task_id, token or "", claims)
    refused = place = None
    if person is not None:
        refused = await terminals.refusal(
            db, person.task_id, person.user_id, hub=device_hub
        )
        if refused is None:
            place = await languages.place(
                db,
                person.task_id,
                person.actor,
                user_id=person.user_id,
                in_workspace=person.in_workspace,
            )
    await db.commit()
    if person is None:
        await websocket.close(code=1008, reason="cannot open this language server")
        return
    await websocket.accept()
    if refused is not None:
        await _refuse(websocket, refused)
        return
    if place is None:
        await _refuse(websocket, "unavailable")
        return
    header = json.dumps(
        {"method": "language", "params": {**place.params, "language": language}}
    )
    try:
        stream = await device_hub.open_executor_stream(
            place.device_id, place.state, header
        )
    except StreamsUnsupported:
        await _refuse(websocket, terminals.Refusal.outdated)
        return
    except DeviceOffline:
        await _refuse(websocket, "unavailable")
        return
    up: asyncio.Task[None] | None = None
    incoming: asyncio.Task[Any] | None = None
    review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
    try:
        try:
            answer, rest = await asyncio.wait_for(_answer(stream), ANSWER_WITHIN_S)
        except TimeoutError:
            answer, rest = None, b""
        reason = _refusal(answer)
        if reason is not None:
            await _refuse(websocket, reason)
            return
        assert answer is not None  # a refusal otherwise
        await websocket.send_text(
            json.dumps({"type": "ready", "root": answer["result"]["root"]})
        )
        up = asyncio.create_task(_pass_up(websocket, stream, rest))
        while True:
            if incoming is None:
                incoming = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait(
                {incoming, review, up}, return_when=asyncio.FIRST_COMPLETED
            )
            if up in done:
                up.exception()  # a page gone mid-send ends it as a close does
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
                continue
            message = incoming.result()
            incoming = None
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                await stream.send(data)
    except (WebSocketDisconnect, DeviceOffline):
        pass
    finally:
        review.cancel()
        if up is not None:
            up.cancel()
        if incoming is not None:
            incoming.cancel()
        # The machine is told even when this handler is cancelled (the page
        # went with its server process): a stream nobody closes keeps the
        # language server running.
        await asyncio.shield(stream.close())
