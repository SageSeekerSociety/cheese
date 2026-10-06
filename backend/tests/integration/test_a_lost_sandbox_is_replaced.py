"""A session whose sandbox's host stopped answering works on in a new sandbox,
and its agent is told so once.

Sandboxes are disposable: what counts is what was pushed. The hosts, the
bucket and the pool here are the ones of ``test_sandbox_idle_stop``; a tool
call asks for its hands the way the session's client does.
"""

from datetime import datetime, timedelta

from app.domain.machine import services
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    host_comes_up,
    maintain,
    room_lines,
    working_on,
)

NOTICE = (
    "原来的沙箱所在机器失联，已换成一个新沙箱：工作区从 git 重新取出，"
    "上次推送之后没推送的改动不在了。"
)

ROOM_LINE = (
    "沙箱所在的机器不再响应，沙箱已换成新的："
    "新沙箱从仓库里已推送的内容开始，没推送的改动不在了"
)


def _later(monkeypatch, by: timedelta) -> None:
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + by

    monkeypatch.setattr(services, "datetime", Later)


def _tool_call(case, seat, *, tells_agent: bool) -> dict:
    """A tool call's request for its hands. ``tells_agent``: the caller puts
    what the platform says beside the tool's result, as a tool the agent
    called does and a project hook does not."""
    return case.client.post(
        f"/topics/{seat.room}/sessions/{seat.session}/work-lease",
        headers={"X-Cheese-Token": seat.token},
        json={"env": {}, "timeout": 5, "tells_agent": tells_agent},
    ).json()["data"]


def test_the_agent_is_told_once_that_its_sandbox_was_replaced(cloud, monkeypatch):
    lost_seat, other_seat = cloud.seats[0], cloud.seats[1]
    working_on(cloud, lost_seat, "host-a")
    working_on(cloud, other_seat, "host-b")
    cloud.hosts.online.discard("host-a")
    maintain(cloud)
    _later(monkeypatch, timedelta(minutes=11))
    maintain(cloud)

    assert _tool_call(cloud, lost_seat, tells_agent=True).get("preparing")
    host_comes_up(cloud, lost_seat, "host-c")
    # A project hook's call takes the new sandbox and is told nothing: nobody
    # would read it there.
    hook = _tool_call(cloud, lost_seat, tells_agent=False)
    assert hook["target"]["device_id"] == "host-c"
    assert "notice" not in hook

    told = _tool_call(cloud, lost_seat, tells_agent=True)
    again = _tool_call(cloud, lost_seat, tells_agent=True)

    assert told["target"]["device_id"] == "host-c"
    assert told["notice"] == NOTICE
    assert "notice" not in again
    assert ROOM_LINE in room_lines(cloud, lost_seat)
    # The session on the host that answers keeps its sandbox and hears nothing.
    other = _tool_call(cloud, other_seat, tells_agent=True)
    assert other["target"]["device_id"] == "host-b"
    assert "notice" not in other
