"""A session whose sandbox's host stopped answering works on in a new sandbox,
and its agent is told so once.

Sandboxes are disposable: what counts is what each turn's checkpoint pushed
and backed up. The hosts, the
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
    maintain,
    run,
    working_on,
)

NOTICE = (
    "沙箱环境已换成新的。每轮结束时的检查点存下的东西都还在：已推送的提交在任务"
    "分支上，当时没提交的改动和未跟踪文件在平台快照里，用 "
    'cd "$(cheese worktree <任务 id>)" 重新打开任务目录时自动放回，并说明放回了'
    "什么。检查点之后才做的改动、依赖和缓存、生成目录、/tmp、正在运行的进程不在"
    "了，需要的重新做、重新安装、重新启动。"
)

ROOM_LINE = (
    "环境不再响应，已换成新的：每轮结束时推送和备份的工作会带过来，之后才做的改动不在了"
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
