"""A Codex seat's runner archive crosses to the session host only when the host
lacks it.

The channel as a room's sends reach it, against a session host that keeps the
archives it was given by digest and the one runner of the seat, and keeps every
launch program it was sent.
"""

import ast
import base64
import json

import pytest

from app.domain.agent.harness import Opening, SessionRef
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.codex import launch as codex_launch
from app.domain.agent.harness.codex.channel import CodexChannel
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.test_central_room_sessions import AGENT, channel

#: A project with its agent and one room in it.
room = central_sessions.room


class CodexHost:
    """The session host as a Codex launch finds it (``host.configure``): a
    running runner keeps its execution target, owner and teammate, and is moved
    to the model a launch asks for."""

    def __init__(self) -> None:
        self.archives: set[str] = set()
        self.programs: list[str] = []
        self.model: str | None = None

    async def exec(self, device_id, argv, *, stdin=None, timeout=None, **_):
        assert (device_id, argv) == ("center", ["python3", "-"])
        self.programs.append(stdin)
        line = next(line for line in stdin.splitlines() if line.startswith("payload="))
        payload = json.loads(
            ast.literal_eval(line.removeprefix("payload=json.loads(")[:-1])
        )
        digest = payload["digest"]
        if "archive" in payload:
            self.archives.add(digest)
        if digest not in self.archives:
            return {"exit": 0, "stdout": json.dumps({"runner": "missing"})}
        self.model = payload["config"]["opening"]["model"]
        answer = {
            "thread_id": "codex-thread",
            "alive": True,
            "pid": 1,
            "capabilities": ["long_poll"],
        }
        return {"exit": 0, "stdout": json.dumps(answer)}


def codex_seat(client, monkeypatch) -> tuple[CodexHost, CodexChannel]:
    central = channel(client, monkeypatch)
    host = CodexHost()
    central._hub.exec = host.exec
    return host, CodexChannel(central, ClaudeLaunch("system").execution)


def _carried_archive(program: str) -> bool:
    return len(program) > len(base64.b64encode(codex_launch.build()))


@pytest.mark.anyio
async def test_codex_is_sent_its_runner_only_when_the_host_does_not_hold_it(
    client, room, monkeypatch
):
    project, topic = room
    host, codex = codex_seat(client, monkeypatch)
    session = SessionRef(project, topic, AGENT, harness="codex")
    opening = Opening("System", model="fixture")

    async def exercise():
        await codex.ensure(session, opening)
        assert [_carried_archive(each) for each in host.programs] == [False, True]

        # The next launch the host is asked for (a restarted backend, say).
        host.programs.clear()
        await codex.ensure(session, opening)
        assert [_carried_archive(each) for each in host.programs] == [False]

        # A deploy changed the runner.
        built = codex_launch.build()
        monkeypatch.setattr(codex_launch, "build", lambda: built + b"\n")
        host.programs.clear()
        await codex.ensure(session, opening)
        assert [_carried_archive(each) for each in host.programs] == [False, True]

    client.portal.call(exercise)
