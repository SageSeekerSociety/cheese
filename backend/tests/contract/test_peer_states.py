"""The five states the far side of the executor link can be in, as one table.

A backend never talks to a machine directly. It asks the connection owner over
HTTP, the owner holds the device WebSocket, and the machine answers in frames.
Every way that far side can fail therefore crosses two encodings — an exception
becomes a status plus a body plus, in one case, a header, and becomes an
exception again on the other side — and each crossing is a place the state can
be lost. Each one that was lost cost the same thing twice: whoever was in the
room read a sentence about THIS server, and the alert channel got one too.

So the five are written down once, with what each must come back as:

  * `离线` — no link at all. #1114: the 409 lost `X-Device-Id` on the way out, so
    the client could not tell an absent machine from a broken call, and a room
    whose machine was simply off spent the full startup wait pinging it.
  * `在线不答` — the link is up and nothing comes back. #1118: unclaimed, this
    reached the owner's catch-all and answered 500「服务器内部错误」, a fault in
    the owner process, which is the one thing a silent device is not.
  * `socket 不存在` — the connector could not reach the runner's socket. #1248:
    logged at ERROR, so one machine reconnecting produced one alert per
    reconnect.
  * `拒绝凭据` — the owner refused the caller's secret. This one is NOT a state
    of the machine: it says this deployment is misconfigured, and must never be
    read as a machine having gone away.
  * `机器自己报错` — the runner ran the call and answered with a failure of its
    own. #1231: raised as a bare RuntimeError it became the owner's 500, then
    the backend's 500 on top of it, and the machine's words — the only part
    anyone could act on — were dropped from the one that reached the room.

Three columns are asserted for each: the exception the backend restores, the
level this process logs it at, and the words that travel on to whoever is
waiting. The machine's half is P3's frame fixtures (`tests/support/wire`)
answering a real `DeviceHub`, and the owner is its real ASGI app, so the only
thing stood in for here is the machine.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app import device_connection_app
from app.api.routes.connector import recover_business_state
from app.core.config import settings
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline, device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub
from app.domain.agent.platform_failures import (
    DEVICE_OFFLINE_MESSAGE,
    HOST_UNREACHABLE,
    HOST_UNREACHABLE_CODE,
    classify_platform_failure,
)
from tests.support import wire

OWNER_SECRET = "test-owner-secret"
DEVICE = "machine-7"
STATE = "/home/cheese/rooms/8b1f2a/.cheese/executor"

#: The connector's own words when the runner's socket is not there, taken from
#: the committed wire fixture rather than retyped — a hand-typed copy drifts
#: away from what the Go side actually sends, silently.
SOCKET_GONE: str = next(
    fixture["meaning"]["error"]
    for fixture in wire.fixtures()
    if fixture["type"] == "execution.result" and fixture["meaning"]["error"]
)

#: The runner's own words when the home it was asked about is gone. This one
#: arrives INSIDE the executor's JSON answer rather than on the result frame,
#: which is the whole difference between this state and the one above: the
#: connector reached the socket, and what was behind it said no.
RUNNER_SAID_NO = f"lstat {STATE}: no such file or directory"

#: A 4xx/5xx as a bare number in a sentence somebody reads. `pi/channel.py` puts
#: the transport's own message in front of the person who asked, and 「500
#: Internal Server Error for url …/call/call_executor」 names the pipe an answer
#: did not come back through and nothing about why.
_BARE_HTTP_STATUS = re.compile(r"\b[45]\d\d\b")


@dataclass(frozen=True)
class PeerState:
    """One state of the far side, and what it must come back as."""

    name: str

    # How the peer is put into this state.
    attached: bool = True
    secret: str = OWNER_SECRET
    #: The frames the machine answers with, or None for one that never answers.
    answer: Callable[[str], list[dict[str, Any]]] | None = None
    timeout: float = 10.0

    # What the owner puts on the wire.
    wire_status: int = 502
    names_the_device: bool = False

    # The three columns.
    restores_as: type[BaseException] = DeviceCallError
    #: A substring the restored exception's own message must carry.
    words: str = ""
    #: The `app.errors` records this state writes, in order.
    logged: tuple[tuple[str, str], ...] = ()


PEER_STATES: tuple[PeerState, ...] = (
    PeerState(
        name="离线",
        attached=False,
        wire_status=409,
        names_the_device=True,
        restores_as=DeviceOffline,
        words=f"device {DEVICE} is offline",
    ),
    PeerState(
        name="在线不答",
        answer=None,
        # Not a wait: `call_executor` arms `asyncio.wait_for` with exactly this,
        # so the state is reached as fast as the loop can reach it.
        timeout=0.05,
        wire_status=504,
        restores_as=TimeoutError,
        words=f"device {DEVICE} did not answer call_executor in time",
    ),
    PeerState(
        name="socket 不存在",
        answer=lambda call_id: [wire.execution_result(call_id, error=SOCKET_GONE)],
        wire_status=502,
        restores_as=DeviceCallError,
        words=SOCKET_GONE,
        logged=(("WARNING", "device_call_failed"),),
    ),
    PeerState(
        name="拒绝凭据",
        secret="not-the-owners-secret",
        wire_status=403,
        restores_as=httpx.HTTPStatusError,
        words="403",
    ),
    PeerState(
        name="机器自己报错",
        answer=lambda call_id: [
            wire.execution_data(
                call_id, json.dumps({"error": RUNNER_SAID_NO}).encode()
            ),
            wire.execution_result(call_id),
        ],
        wire_status=502,
        restores_as=DeviceCallError,
        words=RUNNER_SAID_NO,
        logged=(("WARNING", "device_call_failed"),),
    ),
)

IDS = [state.name for state in PEER_STATES]

#: The two whose words belong to the machine rather than to the platform.
RELAYED = [state for state in PEER_STATES if state.restores_as is DeviceCallError]
RELAYED_IDS = [state.name for state in RELAYED]


def _event(record: logging.LogRecord) -> str:
    """The event name, whether the line was written through structlog or not."""
    if isinstance(record.msg, dict):
        return str(record.msg.get("event", ""))
    return record.getMessage()


@pytest.fixture(autouse=True)
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """This process running as the connection owner, with nothing left over."""
    monkeypatch.setattr(settings, "device_connection_owner", True)
    monkeypatch.setattr(settings, "device_connection_secret", OWNER_SECRET)
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    monkeypatch.setattr(device_connection_app, "_active_rpc_calls", 0)
    monkeypatch.setattr(device_connection_app, "_executor_calls", {})
    device_hub._devices.clear()
    device_hub._screens.clear()
    device_hub._by_screen_token.clear()


async def _machine(state: PeerState) -> wire.RecordingDevice:
    """A connected machine with a ready executor, unless the state is `离线`."""
    connector = wire.RecordingDevice()
    if state.attached:
        await device_hub.attach_device(DEVICE, connector)
        await connector.sent.get()  # the welcome frame
        await device_hub.on_device_message(
            DEVICE, {"t": "hello", "v": 3, "executor": True}
        )
    return connector


async def _let_the_machine_answer(
    state: PeerState, connector: wire.RecordingDevice
) -> None:
    if not state.attached or state.answer is None:
        return
    call = await connector.next_call()
    for frame in state.answer(call.id):
        await device_hub.on_device_message(DEVICE, frame)


async def _ask(state: PeerState) -> BaseException:
    """Put the peer in this state, ask it for one call, return what came back."""
    connector = await _machine(state)
    backend = RemoteDeviceHub(
        "http://owner",
        state.secret,
        transport=httpx.ASGITransport(app=device_connection_app.app),
    )
    asked = asyncio.ensure_future(
        backend.call_executor(
            DEVICE, STATE, "invoke", {"tool": "Bash"}, timeout=state.timeout
        )
    )
    try:
        await _let_the_machine_answer(state, connector)
        with pytest.raises(BaseException) as raised:  # noqa: PT011,B017 — the type IS the column
            await asyncio.wait_for(asked, 5)
    finally:
        await backend.close()
    return raised.value


async def _on_the_wire(state: PeerState) -> httpx.Response:
    """The owner's own answer, before any client turns it back into a type."""
    connector = await _machine(state)
    body = {
        "device_id": DEVICE,
        "state": STATE,
        "method": "invoke",
        "params": {"tool": "Bash"},
        "timeout": state.timeout,
        "trace_id": f"execution-{state.name}",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=device_connection_app.app),
        base_url="http://owner",
        headers={"X-Device-Connection-Secret": state.secret},
    ) as client:
        posted = asyncio.ensure_future(
            client.post("/internal/device-connection/call/call_executor", json=body)
        )
        await _let_the_machine_answer(state, connector)
        return await asyncio.wait_for(posted, 5)


def test_every_peer_state_is_in_the_table() -> None:
    """Five, and named — a state with no row is a state nobody encoded."""
    assert IDS == ["离线", "在线不答", "socket 不存在", "拒绝凭据", "机器自己报错"]


@pytest.mark.parametrize("state", PEER_STATES, ids=IDS)
async def test_the_owner_puts_the_state_on_the_wire(state: PeerState) -> None:
    """Status, and the one header that carries what no status can."""
    response = await _on_the_wire(state)
    assert response.status_code == state.wire_status
    assert (response.headers.get("X-Device-Id") == DEVICE) is state.names_the_device


@pytest.mark.parametrize("state", PEER_STATES, ids=IDS)
async def test_the_backend_restores_the_state_the_owner_sent(state: PeerState) -> None:
    """Column one: a caller reads one type whichever side of the owner it runs on."""
    failure = await _ask(state)
    assert type(failure) is state.restores_as
    assert state.words in str(failure)


@pytest.mark.parametrize("state", PEER_STATES, ids=IDS)
async def test_what_this_process_logs_about_the_far_side(
    state: PeerState, caplog: pytest.LogCaptureFixture
) -> None:
    """Column two, and the invariant under it: none of the five is an ERROR.

    Every one of them is a fact about somebody else's machine, or about this
    deployment's own configuration, and an ERROR here is an alert claiming this
    server broke. #1231 sent 17 of those folding 29 repeats in a day; #1248 sent
    one per reconnect.
    """
    with caplog.at_level(logging.DEBUG, logger="app.errors"):
        await _ask(state)
    records = [r for r in caplog.records if r.name == "app.errors"]
    assert [(r.levelname, _event(r)) for r in records] == list(state.logged)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.parametrize("state", RELAYED, ids=RELAYED_IDS)
async def test_the_machines_own_words_are_not_rewritten(state: PeerState) -> None:
    """Column three, for the two states whose words belong to the machine.

    They are the whole of what the person in the room can act on, so the
    platform relays them and adds nothing of its own.
    """
    failure = await _ask(state)
    assert str(failure) == state.words


async def test_an_offline_machine_reaches_the_agent_without_a_status_code() -> None:
    """Column three for `离线`, the one state the agent reads a sentence about.

    Two halves, and the seam runs between them: the transport has to restore
    `DeviceOffline` (it can only do that from `X-Device-Id`), and the platform
    turns that into a sentence of its own rather than passing a transport error
    along. Drop either and what arrives in front of whoever asked is `Client
    error '409 Conflict' for url …` — the pipe, and a bare status code.
    """
    failure = await _ask(PEER_STATES[0])
    assert isinstance(failure, DeviceOffline)
    assert failure.device_id == DEVICE
    assert not _BARE_HTTP_STATUS.search(str(failure)), str(failure)

    said = classify_platform_failure(failure, code=HOST_UNREACHABLE_CODE)
    assert said is HOST_UNREACHABLE
    for sentence in (DEVICE_OFFLINE_MESSAGE, said.content, said.detail):
        assert not _BARE_HTTP_STATUS.search(sentence), sentence


def test_the_sentence_an_offline_machine_gets_promises_only_what_happens() -> None:
    """The topic does not move, and the words must not say it will.

    `device_provider.resolve_device` never re-pins a bound topic, and
    `DEVICE_OFFLINE_MESSAGE` tells the person exactly that — 「不会漂到别的设备，
    以免工作树/会话错乱」. A detail line promising the platform will carry the
    topic elsewhere describes a mechanism that is not there, and sends whoever
    reads it off to wait for a move that never comes.
    """
    assert "换到别的设备" not in HOST_UNREACHABLE.detail
    assert "重新连" in HOST_UNREACHABLE.detail


async def test_a_409_that_is_not_the_owners_offline_is_not_read_as_one() -> None:
    """`X-Device-Id` is the only thing that makes a 409 mean 「that machine is gone」.

    Reading a device name out of a 409 that did not carry one raised KeyError
    from inside the transport, which surfaced as whatever the caller happened to
    catch — `pi entry read failed` on a poller, a 500 on the control route — and
    the body that would have said what sent it was lost (2026-09-16).
    """
    backend = RemoteDeviceHub("http://owner", OWNER_SECRET)
    backend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(409, text="device calls are active")
        ),
        base_url="http://owner",
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await backend._request("POST", "/internal/device-connection/call/exec")
    assert raised.value.response.status_code == 409
    assert "device calls are active" in raised.value.response.text


@pytest.mark.parametrize("state", PEER_STATES, ids=IDS)
async def test_no_peer_state_leaves_the_owner_counting_a_call_it_finished(
    state: PeerState,
) -> None:
    """The counter gates release-drain: one call leaked leaves the owner
    permanently 「busy」 and blocks its own deploy."""
    await _ask(state)
    assert device_connection_app._active_rpc_calls == 0


class _Chat:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def recover_sessions(self, device_id: str) -> int:
        raise self._failure


class _Wakeup:
    async def wake_device(self, device_id: str) -> None:
        return None


@pytest.fixture
def quiet_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.background.spawn", lambda coro, *, name: coro.close())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: _Wakeup())


@pytest.mark.parametrize(
    ("failure", "says"),
    [
        (DeviceOffline(DEVICE), DEVICE),
        (DeviceCallError(SOCKET_GONE), SOCKET_GONE),
    ],
    ids=["离线", "socket 不存在"],
)
async def test_a_peer_state_on_reconnect_is_waited_out_not_alerted(
    failure: Exception,
    says: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    quiet_reconnect: None,
) -> None:
    """Column two, one hop further in. Every device that connects has its
    sessions recovered, and a machine that is not ready yet answers that call in
    one of these same states. Its next connection runs recovery again, which is
    what makes it a state to wait out rather than an alert."""
    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: _Chat(failure))
    with caplog.at_level(logging.WARNING, logger="app.api.routes.connector"):
        await recover_business_state(DEVICE)
    records = [r for r in caplog.records if r.name == "app.api.routes.connector"]
    assert [r.levelname for r in records] == ["WARNING"]
    # The line has to name the machine this is about, or the words it said —
    # 「recovery failed」 with neither is what nobody could act on.
    assert says in records[0].getMessage()


async def test_a_recovery_failure_that_is_not_a_peer_state_is_still_an_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    quiet_reconnect: None,
) -> None:
    """The other side of the same line. Nothing above widens into 「recovery
    never fails loudly」: a fault of ours on that path is an ERROR as before."""
    monkeypatch.setattr(
        "app.api.deps.get_chat_service", lambda: _Chat(ValueError("bad row"))
    )
    with caplog.at_level(logging.WARNING, logger="app.api.routes.connector"):
        await recover_business_state(DEVICE)
    records = [r for r in caplog.records if r.name == "app.api.routes.connector"]
    assert [r.levelname for r in records] == ["ERROR"]


async def test_one_session_in_a_peer_state_does_not_stop_the_others() -> None:
    """The same standing inside `recover_sessions`: the topic whose machine said
    no is skipped, every other session is still recovered."""
    import uuid

    from app.domain.agent.chat import ChatService

    replayed = []
    sessions = [
        type("S", (), {"topic_id": uuid.uuid4()})(),
        type("S", (), {"topic_id": uuid.uuid4()})(),
    ]

    class Compute:
        async def recover_sessions(self, device_id):
            return sessions

        def work_in_flight(self, topic_id):
            return None

        async def replay(self, session, known_texts):
            if session is sessions[0]:
                raise DeviceCallError(SOCKET_GONE)
            replayed.append(session)

    async def _said():
        return set()

    chat = ChatService.__new__(ChatService)
    chat._compute = Compute()
    chat._said = lambda session: _said()

    assert await chat.recover_sessions(DEVICE) == 2
    assert replayed == [sessions[1]]
