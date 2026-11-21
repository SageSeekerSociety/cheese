"""A language server beside a task's working copy, from the page's side.

A person who may edit the task opens a socket for a language and is told
where the checkout is; what they write reaches a server running there, in
their name, and its answers come back whole. The socket and the server end
together. Someone who may not edit the task gets nothing; a language with no
server, a machine without its program, an executor that predates language
servers, and a task whose copy no machine holds awake are refused — and
asking never brings a machine up or counts as using one.

The machine is a real executor with a stand-in server on its ``PATH``
(`tests/support/fake_language_server.py`); the device link in front of it is
the terminal tests' stand-in connector.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from starlette.websockets import WebSocketDisconnect

from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness import harness_for
from app.domain.environment.models import (
    ConversationEnvironment,
    Environment,
    EnvironmentAttachment,
)
from app.domain.machine.models import SandboxHome
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.delivery import delivery_task
from tests.integration.conftest import post_project, seed_user, session_token
from tests.integration.test_a_persons_terminal_in_a_task import (
    DEVICE,
    _Connector,
    _device_of,
)
from tests.support import executor_release, fake_language_server
from tests.support.environments import remember_place

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the executor's socket is a Unix socket here"
)

frame = fake_language_server.frame


class _OldConnector(_Connector):
    """In front of an executor older than language servers: it reads the
    request as a call to a method it does not know, and says so."""

    async def send_json(self, frame: dict) -> None:
        if frame.get("t") != "stream.open":
            await super().send_json(frame)
            return
        answer = json.dumps({"error": "Unknown executor method"}).encode() + b"\n"
        self._up(
            {
                "t": "stream.data",
                "id": frame["id"],
                "data": base64.b64encode(answer).decode(),
            }
        )
        self._up({"t": "stream.close", "id": frame["id"]})


class _Task:
    def __init__(self, task_id, checkout, state, program):
        self.id, self.checkout, self.state, self.program = (
            task_id,
            checkout,
            state,
            program,
        )


@pytest.fixture
def task(client, tmp_path):
    """A task whose environment is on ``DEVICE`` with its executor running
    and the stand-in server on its ``PATH``. Alice may edit it and is not
    attached to it yet; its AI teammate works there."""
    runtime = executor_release.install(tmp_path / "platform")
    home = tmp_path / "home"
    (home / "room").mkdir(parents=True)
    program = fake_language_server.install(tmp_path / "bin")
    state = tmp_path / "state"
    subprocess.run(
        [sys.executable, str(runtime), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(home / "room"),
                "env": {"HOME": str(home), "PATH": str(program.parent)},
            }
        ),
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
    made = delivery_task(client, room)
    assert made is not None
    checkout = home / ".cheese" / "tasks" / str(made.id)
    checkout.mkdir(parents=True)

    async def seat():
        async with client.test_factory() as db:
            topic = await db.get(Topic, made.room_id)
            assert topic is not None
            await remember_place(
                db,
                conversation_id=made.id,
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
            await db.commit()
        await device_hub.attach_device(DEVICE, _connector(client))
        await device_hub.on_device_message(
            DEVICE, {"t": "hello", "v": 1, "executor": True, "streams": True}
        )

    client.portal.call(seat)
    try:
        yield _Task(made.id, checkout, state, program)
    finally:
        subprocess.run(
            [sys.executable, str(runtime), "stop", "--state", str(state)],
            capture_output=True,
            timeout=30,
        )


def _connector(client, kind=_Connector) -> Any:
    """The stand-in connector, on the loop the app serves from."""
    return kind(asyncio.get_running_loop())


def _url(task_id, language: str, handle: str = "alice") -> str:
    return (
        f"/connector/tasks/{task_id}/languages/{language}?token={session_token(handle)}"
    )


class _Page:
    """The page's end of the socket: LSP messages out of BINARY frames."""

    def __init__(self, ws):
        self.ws, self.buffer = ws, bytearray()

    def first(self) -> dict:
        return self.ws.receive_json()

    def _more(self):
        message = self.ws.receive()
        if message.get("bytes") is None:
            raise EOFError(message)
        self.buffer.extend(message["bytes"])

    def message(self) -> dict:
        while b"\r\n\r\n" not in self.buffer:
            self._more()
        head, _, rest = bytes(self.buffer).partition(b"\r\n\r\n")
        length = int(head.split(b":", 1)[1])
        while len(rest) < length:
            self._more()
            head, _, rest = bytes(self.buffer).partition(b"\r\n\r\n")
        self.buffer[:] = rest[length:]
        return json.loads(rest[:length])

    def ask(self, method, params=None, request_id=1) -> dict:
        self.ws.send_bytes(
            frame(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
        )
        return self.message()


def _gone(pid, within=10.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        stat = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        if not stat or stat.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def _db(client, work):
    async def run():
        async with client.test_factory() as db:
            return await work(db)

    return client.portal.call(run)


def test_a_person_talks_to_a_server_in_the_tasks_checkout(client, task):
    with client.websocket_connect(_url(task.id, "c")) as ws:
        page = _Page(ws)
        assert page.first() == {"type": "ready", "root": str(task.checkout.resolve())}
        assert page.ask("echo", {"text": "héllo"})["result"] == {"text": "héllo"}

        # Frames mean nothing: two requests cut into pieces are two requests.
        data = frame({"jsonrpc": "2.0", "id": 2, "method": "echo", "params": [2]})
        data += frame({"jsonrpc": "2.0", "id": 3, "method": "echo", "params": [3]})
        for at in range(0, len(data), 5):
            ws.send_bytes(data[at : at + 5])
        assert [page.message()["result"], page.message()["result"]] == [[2], [3]]


def test_the_server_runs_in_the_persons_name_not_the_teammates(client, task):
    with client.websocket_connect(_url(task.id, "cpp")) as ws:
        page = _Page(ws)
        page.first()
        who = page.ask("whoami")["result"]

    async def names(db):
        alice = await db.scalar(select(User.id).where(User.username == "alice"))
        rows = (
            await db.execute(
                select(EnvironmentAttachment.id, EnvironmentAttachment.user_id).where(
                    EnvironmentAttachment.conversation_id == task.id
                )
            )
        ).all()
        return {str(row.id): row.user_id == alice for row in rows}

    attachments = _db(client, names)
    assert who["cwd"] == str(task.checkout.resolve())
    assert who["author"] == "alice"
    assert attachments.get(who["attachment"]) is True


def test_closing_the_socket_ends_the_server(client, task):
    with client.websocket_connect(_url(task.id, "c")) as ws:
        page = _Page(ws)
        page.first()
        pid = page.ask("whoami")["result"]["pid"]
    assert _gone(pid)


def test_the_server_ending_closes_the_socket(client, task):
    with client.websocket_connect(_url(task.id, "c")) as ws:
        _Page(ws).first()
        ws.send_bytes(frame({"jsonrpc": "2.0", "method": "exit"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_bytes()
        assert closed.value.code == 1000


def test_someone_who_may_not_edit_the_task_gets_nothing(client, task):
    seed_user(client, "mallory")
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url(task.id, "c", "mallory")) as ws:
            ws.receive()
    assert closed.value.code == 1008


def test_a_language_with_no_server_is_unavailable(client, task):
    with client.websocket_connect(_url(task.id, "cobol")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "unavailable"}


def test_a_machine_without_the_servers_program_is_unavailable(client, task):
    task.program.unlink()
    with client.websocket_connect(_url(task.id, "c")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "unavailable"}


def test_an_executor_older_than_language_servers_is_outdated(client, task):
    async def old():
        await device_hub.attach_device(DEVICE, _connector(client, _OldConnector))
        await device_hub.on_device_message(
            DEVICE, {"t": "hello", "v": 1, "executor": True, "streams": True}
        )

    client.portal.call(old)
    with client.websocket_connect(_url(task.id, "c")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "outdated"}


def test_a_third_server_for_the_same_task_is_refused(client, task):
    with (
        client.websocket_connect(_url(task.id, "c")) as one,
        client.websocket_connect(_url(task.id, "cpp")) as two,
    ):
        assert one.receive_json()["type"] == "ready"
        assert two.receive_json()["type"] == "ready"
        with client.websocket_connect(_url(task.id, "c")) as three:
            assert three.receive_json() == {"type": "refused", "reason": "limit"}


def _state_of_the_machine(client, task):
    async def read(db):
        environments = await db.scalar(select(func.count()).select_from(Environment))
        pointed = await db.scalar(
            select(ConversationEnvironment.environment_id).where(
                ConversationEnvironment.conversation_id == task.id
            )
        )
        environment = await db.get(Environment, pointed)
        alice = await db.scalar(select(User.id).where(User.username == "alice"))
        attached = await db.scalar(
            select(func.count())
            .select_from(EnvironmentAttachment)
            .where(EnvironmentAttachment.user_id == alice)
        )
        return {
            "environments": environments,
            "pointed": pointed,
            "preparing_until": environment.preparing_until,
            "device_id": environment.device_id,
            "alice_attached": attached,
        }

    return _db(client, read)


def test_no_machine_holding_the_copy_is_unavailable_and_none_is_brought_up(
    client, task
):
    async def gone(db):
        await db.execute(
            update(Environment)
            .where(Environment.device_id == DEVICE)
            .values(device_id=None)
        )
        await db.commit()

    _db(client, gone)
    before = _state_of_the_machine(client, task)

    with client.websocket_connect(_url(task.id, "c")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "unavailable"}

    # No environment made or re-pointed, no bring-up started, nobody attached.
    assert _state_of_the_machine(client, task) == before
    assert before["preparing_until"] is None and before["alice_attached"] == 0


def _sandbox(client, task, *, frozen: bool) -> uuid.UUID:
    """Make the task's environment a sandbox on ``DEVICE`` that sat idle a
    while (frozen, when ``frozen``); answers its home."""
    _device_of(client, "alice")
    stale = datetime.now(UTC) - timedelta(minutes=15)

    async def seed(db):
        home = SandboxHome(
            device_id=DEVICE,
            topic_id=task.id,
            room_resource_id=str(uuid.uuid4()),
            resource_id=str(uuid.uuid4()),
            spec="standard",
            active_at=stale,
            frozen_at=stale if frozen else None,
        )
        db.add(home)
        await db.flush()
        await db.execute(
            update(Environment)
            .where(Environment.device_id == DEVICE)
            .values(sandbox_home_id=home.id)
        )
        await db.commit()
        return home.id

    return _db(client, seed)


def _home(client, home_id):
    async def read(db):
        home = await db.get(SandboxHome, home_id)
        return home.active_at, home.frozen_at

    return _db(client, read)


def test_a_frozen_sandbox_is_unavailable_and_stays_frozen(client, task):
    home = _sandbox(client, task, frozen=True)
    before = _home(client, home)

    with client.websocket_connect(_url(task.id, "c")) as ws:
        assert ws.receive_json() == {"type": "refused", "reason": "unavailable"}

    assert _home(client, home) == before


def test_using_a_language_server_does_not_count_as_using_the_sandbox(client, task):
    home = _sandbox(client, task, frozen=False)
    before = _home(client, home)

    with client.websocket_connect(_url(task.id, "c")) as ws:
        page = _Page(ws)
        assert page.first()["type"] == "ready"
        for number in range(5):
            assert page.ask("echo", [number], request_id=number)["result"] == [number]

    assert _home(client, home) == before
