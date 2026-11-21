"""A person's terminal in a task, from the page's side.

The page lists the person's shells in the task's environment and opens a
socket on one: what they type reaches the shell, its output comes back, the
shell is still there after the socket closes, and closing it ends it. On
someone else's own computer the person is told so instead, and someone who
may not edit the task gets nothing.

The machine is a real executor; the device link in front of it is a stand-in
that relays a stream the way the connector does (`cli/internal/host/stream.go`).
"""

import asyncio
import base64
import contextlib
import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, update
from starlette.websockets import WebSocketDisconnect

from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness import harness_for
from app.domain.agent.harness.claude_code.remote_execution import runtime_core
from app.domain.device.models import DeviceRow
from app.domain.environment.models import (
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.team.services import team_service
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.delivery import delivery_task
from tests.integration.conftest import post_project, seed_user, session_token
from tests.support import executor_release
from tests.support.environments import remember_place

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="a terminal is a POSIX pseudo-terminal"
)

DEVICE = "terminal-device"


class _Connector:
    """The device link's far end: relays each stream the server opens to the
    executor socket, and back, as the connector does."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.sockets: dict[str, socket.socket] = {}

    def _up(self, frame: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            device_hub.on_device_message(DEVICE, frame), self.loop
        )

    def _pump(self, stream_id: str, connection: socket.socket) -> None:
        while True:
            try:
                chunk = connection.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                self._up({"t": "stream.close", "id": stream_id})
                return
            self._up(
                {
                    "t": "stream.data",
                    "id": stream_id,
                    "data": base64.b64encode(chunk).decode(),
                }
            )

    def _call(self, frame: dict) -> None:
        request = json.loads(frame["stdin"])
        answer = runtime_core.request(
            frame["path"], request["method"], request["params"]
        )
        payload = json.dumps({"result": answer}).encode() + b"\n"
        self._up(
            {
                "t": "execution.data",
                "id": frame["id"],
                "data": base64.b64encode(payload).decode(),
            }
        )
        self._up({"t": "execution.result", "id": frame["id"]})

    async def send_json(self, frame: dict) -> None:
        kind, stream_id = frame.get("t"), frame.get("id", "")
        if kind == "execution.call":
            threading.Thread(target=self._call, args=(frame,), daemon=True).start()
        elif kind == "stream.open":
            connection = runtime_core.connect(frame["path"])
            connection.sendall(frame["header"].encode() + b"\n")
            self.sockets[stream_id] = connection
            threading.Thread(
                target=self._pump, args=(stream_id, connection), daemon=True
            ).start()
        elif kind == "stream.data" and stream_id in self.sockets:
            self.sockets[stream_id].sendall(base64.b64decode(frame["data"]))
        elif kind == "stream.close" and stream_id in self.sockets:
            connection = self.sockets.pop(stream_id)
            # Hung up as a close is in Go: the executor hears it at once, even
            # with this side's pump still reading.
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()


@pytest.fixture
def machine(client, tmp_path):
    """A task whose environment is on ``DEVICE``, with its executor running
    and alice attached to it; answers (task id, its checkout, the state)."""
    runtime = executor_release.install(tmp_path / "platform")
    home = tmp_path / "home"
    (home / "room").mkdir(parents=True)
    state = tmp_path / "state"
    subprocess.run(
        [sys.executable, str(runtime), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(home / "room"), "env": {"HOME": str(home)}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    seed_user(client, "alice")
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    room = client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers={"Authorization": f"Bearer {session_token('alice')}"},
    ).json()["data"]["id"]
    task = delivery_task(client, room)
    checkout = home / ".cheese" / "tasks" / str(task.id)
    checkout.mkdir(parents=True)

    links: list = []

    async def seat():
        async with client.test_factory() as db:
            topic = await db.get(Topic, task.room_id)
            await remember_place(
                db,
                conversation_id=task.id,
                agent_handle="cheese",
                environment={
                    "kind": "device",
                    "device_id": DEVICE,
                    "state": str(state),
                },
                runtime_location={
                    "device_id": "session-host",
                    "channel": "central",
                    "resource_id": str(topic.resource_id or topic.id),
                },
                harness=harness_for(None),
            )
            environment = await db.scalar(
                select(ConversationEnvironment.environment_id).where(
                    ConversationEnvironment.conversation_id == task.id
                )
            )
            alice = await db.scalar(select(User.id).where(User.username == "alice"))
            db.add(
                EnvironmentAttachment(
                    environment_id=environment,
                    user_id=alice,
                    conversation_id=task.id,
                    generation=str(uuid.uuid4()),
                )
            )
            await db.commit()
        connector = _Connector(asyncio.get_running_loop())
        links.append(connector)
        await device_hub.attach_device(DEVICE, connector)
        await device_hub.on_device_message(
            DEVICE, {"t": "hello", "v": 1, "executor": True, "streams": True}
        )

    async def unlink():
        # A link left on the shared hub is a machine every later test's sweep
        # still calls.
        for connector in links:
            await device_hub.detach_device(DEVICE, connector)

    client.portal.call(seat)
    try:
        yield task.id, checkout, state
    finally:
        subprocess.run(
            [sys.executable, str(runtime), "stop", "--state", str(state)],
            capture_output=True,
            timeout=30,
        )
        client.portal.call(unlink)


def _headers(handle: str) -> dict:
    return {"Authorization": f"Bearer {session_token(handle)}"}


def _url(task_id, terminal_id: str, handle: str) -> str:
    token = session_token(handle)
    return f"/connector/tasks/{task_id}/terminals/{terminal_id}?token={token}&cols=100&rows=30"


def _read_until(ws, seen, timeout=15.0) -> tuple[str, list[dict]]:
    """Output and control messages until ``seen(output)`` holds; each output
    frame is acked as the page acks it."""
    output, controls = bytearray(), []
    deadline = time.monotonic() + timeout
    while not seen(output.decode(errors="replace"), controls):
        assert time.monotonic() < deadline, output.decode(errors="replace")[-2000:]
        message = ws.receive()
        if message.get("bytes") is not None:
            output.extend(message["bytes"])
            ws.send_text(json.dumps({"type": "ack", "bytes": len(message["bytes"])}))
        elif message.get("text") is not None:
            controls.append(json.loads(message["text"]))
        else:
            break
    return output.decode(errors="replace"), controls


def _listed(client, task_id, handle="alice") -> dict:
    response = client.get(f"/topics/{task_id}/terminals", headers=_headers(handle))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_typing_reaches_the_shell_and_the_shell_outlives_the_page(client, machine):
    task_id, checkout, _ = machine
    with client.websocket_connect(_url(task_id, "t1", "alice")) as ws:
        _read_until(ws, lambda _, c: any(m["type"] == "attached" for m in c))
        ws.send_bytes(b"pwd; echo done-$((20 + 22))\n")
        output, _ = _read_until(ws, lambda o, _: "done-42" in o)
        assert str(checkout.resolve()) in output

    assert _listed(client, task_id) == {
        "terminals": [{"id": "t1", "ended": False, "number": 1, "program": None}],
        "refused": None,
    }
    with client.websocket_connect(_url(task_id, "t1", "alice")) as ws:
        # Opened again, it shows what it wrote before.
        _read_until(ws, lambda o, _: "done-42" in o)

    closed = client.delete(f"/topics/{task_id}/terminals/t1", headers=_headers("alice"))
    assert closed.json()["data"] == {"closed": True}
    assert _listed(client, task_id)["terminals"] == []


def test_a_machine_an_ai_teammate_brought_up_takes_the_persons_first_terminal(
    client, machine
):
    """The task's machine is up because its AI teammate works there; the
    person has never asked for it. Their first terminal opens all the same."""
    task_id, checkout, _ = machine

    async def never_attached():
        async with client.test_factory() as db:
            await db.execute(
                delete(EnvironmentAttachment).where(
                    EnvironmentAttachment.user_id.is_not(None)
                )
            )
            await db.commit()

    client.portal.call(never_attached)
    with client.websocket_connect(_url(task_id, "t1", "alice")) as ws:
        _read_until(ws, lambda _, c: any(m["type"] == "attached" for m in c))
        ws.send_bytes(b"pwd; echo done-$((20 + 22))\n")
        output, _ = _read_until(ws, lambda o, _: "done-42" in o)
        assert str(checkout.resolve()) in output


def _device_of(client, owner_handle: str) -> None:
    """Make ``DEVICE`` the personal computer of ``owner_handle``."""

    async def enroll():
        async with client.test_factory() as db:
            owner = await db.scalar(
                select(User.id).where(User.username == owner_handle)
            )
            team = await team_service(db).ensure_personal_team(owner)
            db.add(
                DeviceRow(
                    device_id=DEVICE,
                    name="laptop",
                    team_id=team.id,
                    owner_user_id=owner,
                    created_at=datetime.now(UTC),
                )
            )
            await db.execute(
                update(Environment)
                .where(Environment.device_id == DEVICE)
                .values(executor=Environment.executor.op("||")({"own": True}))
            )
            await db.commit()

    client.portal.call(enroll)


def test_on_someone_elses_own_computer_only_its_owner_opens_a_terminal(client, machine):
    task_id, _, _ = machine
    seed_user(client, "bob")
    _device_of(client, "bob")

    assert _listed(client, task_id)["refused"] == "not_owner"
    with client.websocket_connect(_url(task_id, "t1", "alice")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "not_owner"}


def test_the_owner_of_the_computer_opens_one(client, machine):
    task_id, _, _ = machine
    _device_of(client, "alice")

    assert _listed(client, task_id)["refused"] is None
    with client.websocket_connect(_url(task_id, "t1", "alice")) as ws:
        _, controls = _read_until(ws, lambda _, c: bool(c))
        assert controls[0]["type"] == "attached"


def test_someone_who_may_not_edit_the_task_gets_nothing(client, machine):
    task_id, _, _ = machine
    seed_user(client, "mallory")

    response = client.get(f"/topics/{task_id}/terminals", headers=_headers("mallory"))
    assert response.status_code in (403, 404)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url(task_id, "t1", "mallory")) as ws:
            ws.receive()
    assert closed.value.code == 1008
