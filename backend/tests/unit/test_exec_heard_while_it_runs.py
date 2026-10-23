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

import httpx
import pytest

from app.core.config import settings
from app.device_connection_app import app as owner_app
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.device_hub import calls
from app.domain.agent.device_hub import device_hub as owner_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub

LINES = ["downloading", '{"executor_download": {"have": 1, "total": 2}}']


class _Machine:
    """The connector at the far end of the owner's link. It answers an exec
    with ``LINES`` on stderr, each sent up as it is written when it streams
    and the exec asked it to, and then with its result."""

    def __init__(self, *, streams: bool = True, answers: bool = True) -> None:
        # A machine of its own per test: the hub keeps every machine it has
        # seen, and what it waits on is bound to that test's event loop.
        self.device_id = f"machine-{uuid.uuid4().hex[:8]}"
        self.streams = streams
        self.answers = answers
        self._answering: set[asyncio.Task] = set()

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
        await owner_hub.on_device_message(
            self.device_id,
            {
                "t": "exec.result",
                "id": msg["id"],
                "stdout": "done",
                "stderr": "\n".join(LINES),
            },
        )


class _OwnerBeforeStreaming(httpx.AsyncBaseTransport):
    """An owner released before it streamed: it never reads `Accept`."""

    def __init__(self) -> None:
        self._owner = httpx.ASGITransport(app=owner_app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request.headers["Accept"]
        return await self._owner.handle_async_request(request)


@pytest.fixture(autouse=True)
def _owner_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")


def _backend(transport: httpx.AsyncBaseTransport | None = None) -> RemoteDeviceHub:
    return RemoteDeviceHub(
        "http://owner",
        settings.device_connection_auth_secret,
        transport=transport or httpx.ASGITransport(app=owner_app),
    )


async def _exec(hub, machine: _Machine, *, timeout: float = 5) -> tuple[dict, list]:
    heard: list[str] = []
    await owner_hub.attach_device(machine.device_id, machine)
    try:
        answer = await hub.exec(
            machine.device_id, ["install"], timeout=timeout, on_stderr=heard.append
        )
    finally:
        await owner_hub.detach_device(machine.device_id, machine)
    return answer, heard


@pytest.mark.anyio
@pytest.mark.parametrize("through_the_owner", [False, True], ids=["hub", "owner"])
async def test_each_stderr_line_is_heard_and_the_answer_is_unchanged(
    through_the_owner: bool,
) -> None:
    hub = _backend() if through_the_owner else owner_hub

    answer, heard = await _exec(hub, _Machine())

    assert heard == LINES
    assert answer["stdout"] == "done"
    assert answer["stderr"] == "\n".join(LINES)


@pytest.mark.anyio
async def test_a_connector_from_before_streaming_still_answers() -> None:
    answer, heard = await _exec(_backend(), _Machine(streams=False))

    assert heard == []
    assert answer["stdout"] == "done"


@pytest.mark.anyio
async def test_an_owner_from_before_streaming_still_answers() -> None:
    answer, heard = await _exec(_backend(_OwnerBeforeStreaming()), _Machine())

    assert heard == []
    assert answer["stdout"] == "done"


@pytest.mark.anyio
async def test_a_streamed_exec_the_machine_never_answers_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calls, "EXEC_REPLY_SLACK_S", 0)
    with pytest.raises(TimeoutError, match="did not answer exec"):
        await _exec(_backend(), _Machine(answers=False), timeout=0.1)


@pytest.mark.anyio
async def test_a_streamed_exec_to_a_machine_that_is_not_there_is_offline() -> None:
    with pytest.raises(DeviceOffline):
        await _backend().exec(
            f"never-{uuid.uuid4().hex[:8]}",
            ["install"],
            timeout=5,
            on_stderr=lambda line: None,
        )
