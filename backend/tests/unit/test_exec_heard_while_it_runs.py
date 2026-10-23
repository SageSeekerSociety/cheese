"""What a command run on a person's computer writes to stderr is heard while
it runs, and the command's answer is the same whether or not anyone listens.

The executor install is one such command, and its download progress is what
its room is shown (`machine.install_progress`). The lines travel from the
connector (`exec.output`, sent only when the exec asks for `stream`), through
the connection owner (a streamed answer, asked for in `Accept`), to the
backend that asked. Connectors and owners already deployed when this shipped
do neither, and with them the same exec still answers, only silently.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app import device_connection_app
from app.core.config import settings
from app.device_connection_app import app as owner_app
from app.domain.agent.device_hub import DeviceOffline, calls
from app.domain.agent.device_hub import device_hub as owner_hub
from app.domain.agent.device_hub_rpc import STREAMED_EXEC, RemoteDeviceHub

LINES = ["downloading", '{"executor_download": {"have": 1, "total": 2}}']
#: How long the machine waits for the backend to hear a line before it goes on
#: as though it had: a backend that hears nothing until the command ends never
#: hears it in time.
HEARD_WITHIN_S = 2


class _Machine:
    """The connector at the far end of the owner's link. It answers an exec
    with ``LINES`` on stderr, each sent up as it is written when it streams
    and the exec asked it to, and then with its result. A command still
    running is what the lines are for, so the machine does not end the
    command until the backend has heard the line it just wrote, and notes
    each line the backend had not heard in time."""

    def __init__(self, *, streams: bool = True, answers: bool = True) -> None:
        # A machine of its own per test: the hub keeps every machine it has
        # seen, and what it waits on is bound to that test's event loop.
        self.device_id = f"machine-{uuid.uuid4().hex[:8]}"
        self.streams = streams
        self.answers = answers
        self.heard: list[str] = []
        self.unheard_while_running: list[str] = []
        self._news = asyncio.Event()
        self._answering: set[asyncio.Task] = set()

    def hear(self, line: str) -> None:
        """The backend's ``on_stderr``."""
        self.heard.append(line)
        self._news.set()

    async def send_json(self, msg: dict) -> None:
        if msg["t"] == "exec" and self.answers:
            task = asyncio.get_running_loop().create_task(self._answer(msg))
            self._answering.add(task)
            task.add_done_callback(self._answering.discard)

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def _answer(self, msg: dict) -> None:
        if self.streams and msg.get("stream"):
            for line in LINES:
                await owner_hub.on_device_message(
                    self.device_id,
                    {"t": "exec.output", "id": msg["id"], "stderr": line},
                )
                try:
                    async with asyncio.timeout(HEARD_WITHIN_S):
                        while line not in self.heard:
                            self._news.clear()
                            await self._news.wait()
                except TimeoutError:
                    self.unheard_while_running.append(line)
        await owner_hub.on_device_message(
            self.device_id,
            {
                "t": "exec.result",
                "id": msg["id"],
                "stdout": "done",
                "stderr": "\n".join(LINES),
            },
        )


class _Owner(httpx.AsyncBaseTransport):
    """The owner reached the way a backend reaches it: its answer's body
    arrives as the owner writes it. httpx's own ASGI transport hands the
    body over only once the app has finished, which no streamed answer
    survives."""

    def __init__(self, *, reads_accept: bool = True) -> None:
        # False is an owner released before it streamed: it never reads
        # `Accept`, and answers every exec at once.
        self.reads_accept = reads_accept

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = [
            (name.lower(), value)
            for name, value in request.headers.raw
            if self.reads_accept or name.lower() != b"accept"
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path,
            "query_string": request.url.query,
            "root_path": "",
            "headers": headers,
            "server": ("owner", 80),
            "client": ("backend", 1),
        }
        body = await request.aread()
        started: asyncio.Future[tuple[int, list]] = asyncio.Future()
        chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        sent = asyncio.Event()
        read = False

        async def receive() -> dict:
            # The request once, then nothing until the answer is complete:
            # a streamed answer listens for the caller going away meanwhile.
            nonlocal read
            if not read:
                read = True
                return {"type": "http.request", "body": body, "more_body": False}
            await sent.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                started.set_result((message["status"], message.get("headers", [])))
            elif message["type"] == "http.response.body":
                chunks.put_nowait(message.get("body", b""))
                if not message.get("more_body"):
                    chunks.put_nowait(None)
                    sent.set()

        app = asyncio.create_task(owner_app(scope, receive, send))
        status, response_headers = await started

        async def stream() -> AsyncIterator[bytes]:
            while (chunk := await chunks.get()) is not None:
                yield chunk
            await app

        return httpx.Response(status, headers=response_headers, stream=_Body(stream()))


class _Body(httpx.AsyncByteStream):
    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._chunks:
            yield chunk


@pytest.fixture(autouse=True)
def _owner_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")


def _backend(owner: _Owner | None = None) -> RemoteDeviceHub:
    return RemoteDeviceHub(
        "http://owner",
        settings.device_connection_auth_secret,
        transport=owner or _Owner(),
    )


async def _exec(hub, machine: _Machine, *, timeout: float = 5) -> dict:
    await owner_hub.attach_device(machine.device_id, machine)
    try:
        return await hub.exec(
            machine.device_id, ["install"], timeout=timeout, on_stderr=machine.hear
        )
    finally:
        await owner_hub.detach_device(machine.device_id, machine)


@pytest.mark.anyio
@pytest.mark.parametrize("through_the_owner", [False, True], ids=["hub", "owner"])
async def test_each_stderr_line_is_heard_while_the_command_runs(
    through_the_owner: bool,
) -> None:
    machine = _Machine()
    hub = _backend() if through_the_owner else owner_hub

    answer = await _exec(hub, machine)

    assert machine.heard == LINES
    assert machine.unheard_while_running == []
    assert answer["stdout"] == "done"
    assert answer["stderr"] == "\n".join(LINES)


@pytest.mark.anyio
async def test_a_connector_from_before_streaming_still_answers() -> None:
    machine = _Machine(streams=False)

    answer = await _exec(_backend(), machine)

    assert machine.heard == []
    assert answer["stdout"] == "done"


@pytest.mark.anyio
async def test_an_owner_from_before_streaming_still_answers() -> None:
    machine = _Machine()

    answer = await _exec(_backend(_Owner(reads_accept=False)), machine)

    assert machine.heard == []
    assert answer["stdout"] == "done"


@pytest.mark.anyio
async def test_a_streamed_exec_the_machine_never_answers_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calls, "EXEC_REPLY_SLACK_S", 0)
    with pytest.raises(TimeoutError, match="did not answer exec"):
        await _exec(_backend(), _Machine(answers=False), timeout=0.1)


@pytest.mark.anyio
async def test_a_streamed_exec_the_owner_cannot_take_does_not_hold_a_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer backend can send an exec an older owner does not understand.
    It fails, and the owner can still be released afterwards."""
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    async with httpx.AsyncClient(
        base_url="http://owner",
        transport=_Owner(),
        headers={"X-Device-Connection-Secret": "test-owner-secret"},
    ) as owner:
        with pytest.raises(TypeError):
            await owner.post(
                "/internal/device-connection/call/exec",
                headers={"Accept": STREAMED_EXEC},
                json={"device_id": "m", "argv": ["true"], "a_later_option": 1},
            )

        drained = await owner.post("/internal/device-connection/release-drain")
        await owner.post("/internal/device-connection/release-resume")

    assert drained.status_code == 200, drained.text


@pytest.mark.anyio
async def test_a_streamed_exec_to_a_machine_that_is_not_there_is_offline() -> None:
    with pytest.raises(DeviceOffline):
        await _backend().exec(
            f"never-{uuid.uuid4().hex[:8]}",
            ["install"],
            timeout=5,
            on_stderr=lambda line: None,
        )
