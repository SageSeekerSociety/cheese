"""A session whose sandbox's host stopped answering works on in a new sandbox,
and its agent is told so once.

Sandboxes are disposable: what counts is what was pushed. The hosts, the
bucket and the pool here are the ones of ``test_sandbox_idle_stop``; a tool
call asks for its hands the way the session's client does.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.domain.block.models import Block, BlockKind
from app.domain.machine import services
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    host_comes_up,
    host_of,
    maintain,
    room_lines,
    run,
    working_on,
)

NOTICE = (
    "原来的沙箱所在机器失联，已换成一个新沙箱：工作区从 git 重新取出，"
    "上次推送之后没推送的改动不在了。"
)

ROOM_LINE = (
    "环境所在的机器不再响应，环境已换成新的："
    "新环境从仓库里已推送的内容开始，没推送的改动不在了"
)


def _later(monkeypatch, by: timedelta) -> None:
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + by

    monkeypatch.setattr(services, "datetime", Later)


def _said(case, seat) -> list[str]:
    """What the platform said in the room's conversation about its sandbox."""

    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(Block.content)
                    .where(
                        Block.conversation_id == seat.room,
                        Block.kind == BlockKind.event,
                        Block.meta["event_type"].as_string() == "cloud_startup",
                    )
                    .order_by(Block.created_at)
                )
            )

    return run(case, read)


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
    assert _said(cloud, lost_seat) == [ROOM_LINE]
    # The session on the host that answers keeps its sandbox and hears nothing.
    other = _tool_call(cloud, other_seat, tells_agent=True)
    assert other["target"]["device_id"] == "host-b"
    assert "notice" not in other


def test_a_session_whose_host_was_suspended_waits_for_it_and_keeps_its_work(
    cloud, monkeypatch
):
    """A host MicroCloud suspended is woken, not replaced: its session is told
    the sandbox is waking, and gets it back with what it had not pushed."""
    seat, other_seat = cloud.seats[0], cloud.seats[1]
    home = working_on(cloud, seat, "host-a")
    working_on(cloud, other_seat, "host-b")
    machine = host_of(cloud, seat).machine_id
    cloud.provider.machines[machine]["status"] = "suspended"
    cloud.hosts.online.discard("host-a")
    maintain(cloud)
    _later(monkeypatch, timedelta(minutes=11))
    maintain(cloud)

    waiting = _tool_call(cloud, seat, tells_agent=True)

    assert waiting.get("preparing")
    assert waiting["unavailable"] == "沙箱正在唤醒；对话和平台工具仍可用。"
    assert "正在唤醒环境" in room_lines(cloud, seat)
    assert cloud.provider.wakes == [("resume", machine)]

    cloud.hosts.online.add("host-a")
    maintain(cloud)
    back = _tool_call(cloud, seat, tells_agent=True)

    assert back["target"]["device_id"] == "host-a"
    assert "notice" not in back
    assert (home / "room" / "notes.md").read_text() == "not committed anywhere\n"
    assert cloud.provider.deleted == []
    assert _said(cloud, seat) == []
