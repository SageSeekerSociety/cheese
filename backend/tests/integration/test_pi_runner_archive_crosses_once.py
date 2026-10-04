"""pi's runner archive crosses to the session host only when the host lacks it.

The archive is the same half megabyte on every launch until a deploy changes
it, and the host keeps it under its digest; a launch names the digest and the
archive follows only when the host says it does not hold that one.
"""

import ast
import base64
import json

import pytest

from app.domain.agent.harness import PI, SessionRef
from app.domain.agent.harness.pi import launch as pi_launch
from app.domain.agent.room.sessions import RoomSessions
from tests.integration import test_central_room_sessions as central_sessions
from tests.integration.test_central_room_sessions import AGENT, channel, sessions

#: A project with its agent and one room in it.
room = central_sessions.room


class PiHost:
    """The session host as a pi launch finds it: the runner archives it holds,
    by digest, and the one runner of the seat, with the launch it was started
    with. Every launch it is sent is kept, as the program text it ran."""

    def __init__(self) -> None:
        self.archives: set[str] = set()
        self.programs: list[str] = []
        self.contract: str | None = None
        self.pid = 0

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
            answer: dict = {"runner": "missing"}
        else:
            contract = payload["config"]["contract"]
            if contract != self.contract:
                self.contract, self.pid = contract, self.pid + 1
            answer = {
                "session_id": "pi-session",
                "alive": True,
                "pid": self.pid,
                "capabilities": ["long_poll", "live"],
                "contract": self.contract,
            }
        return {"exit": 0, "stdout": json.dumps(answer)}


def pi_seat(client, monkeypatch) -> tuple[PiHost, RoomSessions]:
    central = channel(client, monkeypatch)
    host = PiHost()
    central._hub.exec = host.exec
    return host, sessions(central, PI)


def _carried_archive(program: str) -> bool:
    return len(program) > len(base64.b64encode(pi_launch.build()))


@pytest.mark.anyio
async def test_pi_is_sent_its_runner_only_when_the_host_does_not_hold_it(
    client, room, monkeypatch
):
    project, topic = room
    host, pi = pi_seat(client, monkeypatch)
    session = SessionRef(project, topic, AGENT, harness=PI)

    async def exercise():
        await pi.ensure(session, system_prompt="System", model="fixture")
        assert [_carried_archive(each) for each in host.programs] == [False, True]

        # The next launch the host is asked for (a restarted backend, say).
        restarted = sessions(pi.channel, PI)
        host.programs.clear()
        await restarted.ensure(session, system_prompt="System", model="fixture")
        assert [_carried_archive(each) for each in host.programs] == [False]

        # A deploy changed the runner.
        built = pi_launch.build()
        monkeypatch.setattr(pi_launch, "build", lambda: built + b"\n")
        host.programs.clear()
        await sessions(pi.channel, PI).ensure(
            session, system_prompt="System", model="fixture"
        )
        assert [_carried_archive(each) for each in host.programs] == [False, True]

    client.portal.call(exercise)
