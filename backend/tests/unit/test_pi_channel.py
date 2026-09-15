"""同机 pi：起一个会话，再从一个重启过的后端把它找回来。

The device channel it wraps is the one every other backend uses; what these
check is the part that is pi's — that the screen carries pi's launcher, that the
address written down is the one the connector resolves, and that a backend which
lost its memory finds the session by reading that row back.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent import machine_launcher
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.pi.channel import PiChannel

PROJECT, TOPIC = uuid.uuid4(), uuid.uuid4()


class Hub:
    """A device that answers the few things this channel actually uses."""

    def __init__(self) -> None:
        self.opened: list[HubScreen] = []
        self.shipped: list[str] = []
        self.calls: list[tuple[str, str, str]] = []
        self.session_id = str(uuid.uuid4())
        self.alive = True
        # What the connector reports when nothing is listening on the runner's
        # socket, and what the machine kept of why.
        self.dial_error: str | None = None
        self.runner_log = ""

    def online_device_ids(self):
        return ["dev1"]

    def is_online(self, device_id):
        return device_id == "dev1"

    def all_online_screens(self):
        return list(self.opened)

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ):
        if stdin is not None:
            self.shipped.append(stdin)
        if any("runner.log" in word for word in argv):
            return {
                "stdout": self.runner_log,
                "stderr": "",
                "exit": 0,
                "truncated": False,
            }
        return {"stdout": "", "stderr": "", "exit": 0, "truncated": False}

    async def open_screen(self, device_id, command, **kw) -> HubScreen:
        screen = HubScreen(
            sid=f"s{len(self.opened)}",
            device_id=device_id,
            command=command,
            token="tok",
            agent_user_id=kw["agent_user_id"],
            agent_handle=kw["agent_handle"],
            project_id=kw["project_id"],
            topic_id=kw["topic_id"],
            hook_key=kw.get("hook_key", ""),
        )
        self.opened.append(screen)
        return screen

    def update_screen(self, sid, **fields):
        screen = next(s for s in self.opened if s.sid == sid)
        for name, value in fields.items():
            setattr(screen, name, value)
        return screen

    async def call_executor(self, device_id, state, method, params, **kw):
        self.calls.append((device_id, state, method))
        if self.dial_error is not None:
            raise RuntimeError(self.dial_error)
        if method == "ping":
            return {"alive": self.alive, "session_id": self.session_id}
        return {}


class Rooms:
    """A topic table, without a database. Rows survive across sessions."""

    def __init__(self, placement=None):
        self.room = SimpleNamespace(
            id=TOPIC,
            project_id=PROJECT,
            resource_id=TOPIC,
            is_private=False,
            session_placement=placement,
        )

    def factory(self):
        rooms = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, _model, key):
                return rooms.room if key == TOPIC else None

            async def scalars(self, _statement):
                return [rooms.room] if rooms.room.session_placement else []

            async def commit(self):
                return None

            async def refresh(self, _row):
                return None

        return Session


@pytest.fixture
def channel(monkeypatch):
    def build(rooms: Rooms, hub: Hub) -> PiChannel:
        from app.domain.topic.services import TopicService

        async def locked(_self, topic_id):
            return rooms.room

        monkeypatch.setattr(TopicService, "lock_for_execution", locked)

        async def resolver(_project_id, _topic_id):
            return ("dev1", 1, "agent-x")

        return PiChannel(
            DeviceChannel(
                hub=hub,  # type: ignore[arg-type]
                device_resolver=resolver,
                session_factory=rooms.factory(),
                public_base="http://cheese.test",
            )
        )

    return build


@pytest.mark.anyio
async def test_a_pi_session_starts_on_the_machine_that_holds_the_workspace(channel):
    rooms, hub = Rooms(), Hub()
    handle = await channel(rooms, hub).ensure(
        SessionRef(PROJECT, TOPIC), Opening(system_prompt="房间的提示词")
    )

    # The screen runs pi's launcher, not the other harness's.
    script = hub.shipped[0]
    assert "PI_CODING_AGENT_DIR" in script
    assert "CLAUDE_BIN" not in script
    # Its address is the one the connector will derive, not a second copy.
    assert handle.state == machine_launcher.state_dir(PROJECT, TOPIC, "pi", "agent-x")
    assert handle.session_id == hub.session_id
    assert ("dev1", handle.state, "ping") in hub.calls


DIAL = (
    "dial unix /tmp/cheese-execution-1000-7c754fd98f8e424280793729.sock: "
    "connect: no such file or directory"
)


@pytest.mark.anyio
async def test_a_runner_that_never_bound_reports_its_own_last_words(channel):
    """The runner binds its socket last, so "nothing is listening there" IS
    "the session did not come up" — and the machine is the only place the
    reason is written down.

    Left as the transport's own error this reached a person on 2026-09-15 as a
    bare `500 Internal Server Error for url …/call/call_executor`: the pipe the
    answer did not come back through, and nothing about why. Finding out why
    took an afternoon of one-off probes against the box.
    """
    rooms, hub = Rooms(), Hub()
    hub.dial_error = DIAL
    hub.runner_log = "Traceback (most recent call last):\nRuntimeError: pi 没有握上手"

    with pytest.raises(ScreenSetupError) as refused:
        await channel(rooms, hub).ensure(
            SessionRef(PROJECT, TOPIC), Opening(system_prompt="x")
        )
    assert "pi 没有握上手" in str(refused.value)


@pytest.mark.anyio
async def test_a_machine_that_kept_no_reason_still_reports_the_refusal(channel):
    """A runner that died before it could write anything, or a box that cannot
    be asked, must not turn into a blank refusal — the connector's own sentence
    is still evidence, and it is what names the socket."""
    rooms, hub = Rooms(), Hub()
    hub.dial_error = DIAL
    hub.runner_log = "   \n"

    with pytest.raises(ScreenSetupError) as refused:
        await channel(rooms, hub).ensure(
            SessionRef(PROJECT, TOPIC), Opening(system_prompt="x")
        )
    assert "no such file or directory" in str(refused.value)


@pytest.mark.anyio
async def test_a_restarted_backend_finds_the_session_it_did_not_start(channel):
    """pi 的会话活得比开它的那个后端长，所以位置得写下来。"""
    rooms, hub = Rooms(), Hub()
    started = await channel(rooms, hub).ensure(
        SessionRef(PROJECT, TOPIC), Opening(system_prompt="x")
    )

    # A second channel with no memory of the first, reading only the row.
    found = await channel(rooms, Hub()).discover("dev1")
    assert [handle.state for handle in found] == [started.state]
    assert found[0].agent_handle == "agent-x"


@pytest.mark.anyio
async def test_a_room_running_the_other_harness_is_not_ours_to_recover(channel):
    rooms = Rooms(
        placement={
            "device_id": "dev1",
            "resource_id": str(TOPIC),
            "channel": DeviceChannel.name,
            "runtime": {"harness": "codex", "state": "$HOME/x", "agent_handle": "a"},
        }
    )
    hub = Hub()
    assert await channel(rooms, hub).discover("dev1") == []
    assert hub.calls == [], "a room that is not pi's must not even be probed"


@pytest.mark.anyio
async def test_a_session_whose_runner_never_answered_is_an_error_not_a_handle(channel):
    rooms, hub = Rooms(), Hub()
    hub.alive = False
    with pytest.raises(ScreenSetupError):
        await channel(rooms, hub).ensure(
            SessionRef(PROJECT, TOPIC), Opening(system_prompt="x")
        )
