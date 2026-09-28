"""侧栏红灯的依据 —— `awaiting_reply_since`：有人点了 AI 的名，到现在还没有 AI 回话。

规则是一个没读过代码的人也能判的：人 @ 了 AI 而 AI 一句话都没回，这个房间就在
干等；AI 开口了就不再等；人和人聊天叫不起 AI，也就谈不上在等它；平台自己弹的
提示不是 AI 在回话。灯什么时候亮（等了多久算太久）是前端按当下的钟判的，这里只
钉接口给出的那个时间对不对。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

AGENT = "cheese-0123456789ab"


def _room(client) -> tuple[str, str]:
    ids: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(team_id=await a_team(s), name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.commit()
            ids.update(project=str(project.id), room=str(room.id))

    asyncio.run(_seed())
    return ids["project"], ids["room"]


def _say(
    client,
    project_id: str,
    room_id: str,
    author: str,
    *,
    ago: timedelta,
    summons: bool = False,
    author_type: AuthorType = AuthorType.participant,
    meta: dict | None = None,
) -> datetime:
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    topic_id=uuid.UUID(room_id),
                    kind=BlockKind.message,
                    author_type=author_type,
                    author=author,
                    content="…",
                    created_at=at,
                    meta=meta
                    if meta is not None
                    else {"agent_recipient": {"handle": AGENT, "mentioned": True}}
                    if summons
                    else {},
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _since(client, project_id: str, room_id: str) -> datetime | None:
    rows = client.get(f"/topics?project_id={project_id}").json()["data"]["data"]
    listed = next(t for t in rows if t["id"] == room_id)["awaiting_reply_since"]
    header = client.get(f"/topics/{room_id}").json()["data"]["awaiting_reply_since"]
    assert listed == header
    return datetime.fromisoformat(listed.replace("Z", "+00:00")) if listed else None


def test_an_unanswered_summons_waits_from_the_earliest_message(client):
    pid, rid = _room(client)
    first = _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    _say(client, pid, rid, "bob", ago=timedelta(minutes=2), summons=True)

    since = _since(client, pid, rid)
    assert since is not None
    assert abs(since - first) < timedelta(seconds=1)


def test_an_agent_reply_ends_the_wait(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    _say(client, pid, rid, AGENT, ago=timedelta(minutes=8))

    assert _since(client, pid, rid) is None


def test_a_new_summons_after_the_reply_starts_a_new_wait(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    _say(client, pid, rid, AGENT, ago=timedelta(minutes=8))
    again = _say(client, pid, rid, "alice", ago=timedelta(minutes=6), summons=True)

    since = _since(client, pid, rid)
    assert since is not None
    assert abs(since - again) < timedelta(seconds=1)


def test_people_talking_to_each_other_are_not_waiting_on_the_agent(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9))
    _say(client, pid, rid, "bob", ago=timedelta(minutes=8))

    assert _since(client, pid, rid) is None


def test_a_platform_notice_is_not_the_agent_answering(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    _say(
        client,
        pid,
        rid,
        "cheese",
        ago=timedelta(minutes=8),
        author_type=AuthorType.platform,
    )

    assert _since(client, pid, rid) is not None


# ---- 轮次报错：不等五分钟，立刻算坏 ---------------------------------------


def _failed_at(client, project_id: str, room_id: str) -> str | None:
    rows = client.get(f"/topics?project_id={project_id}").json()["data"]["data"]
    listed = next(t for t in rows if t["id"] == room_id)["turn_failed_at"]
    header = client.get(f"/topics/{room_id}").json()["data"]["turn_failed_at"]
    assert listed == header
    return listed


def _failure(client, pid, rid, *, ago, event_type="turn_failed", severity="error"):
    return _say(
        client,
        pid,
        rid,
        "cheese",
        ago=ago,
        author_type=AuthorType.platform,
        meta={"event_type": event_type, "severity": severity},
    )


def test_a_failed_turn_marks_the_room_until_the_agent_speaks_again(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(seconds=40), summons=True)
    _failure(client, pid, rid, ago=timedelta(seconds=30))
    assert _failed_at(client, pid, rid) is not None

    _say(client, pid, rid, AGENT, ago=timedelta(seconds=10))
    assert _failed_at(client, pid, rid) is None


def test_a_classified_platform_error_counts_too(client):
    pid, rid = _room(client)
    _failure(client, pid, rid, ago=timedelta(seconds=30), event_type="platform_error")
    assert _failed_at(client, pid, rid) is not None


def test_a_warning_is_not_a_failed_turn(client):
    """超时、部署打断是 warn：平台会自己接着跑，不算这一轮坏了。"""
    pid, rid = _room(client)
    _failure(
        client,
        pid,
        rid,
        ago=timedelta(seconds=30),
        event_type="turn_timeout",
        severity="warn",
    )
    _failure(
        client,
        pid,
        rid,
        ago=timedelta(seconds=20),
        event_type="platform_error",
        severity="warn",
    )
    assert _failed_at(client, pid, rid) is None


# ---- PR 反馈 / 检查没过：等 AI 去接手 -------------------------------------


def _task(client, project_id: str, room_id: str) -> str:
    from app.domain.room_task.models import Task

    ids: list[str] = []

    async def _add() -> None:
        async with client.test_factory() as s:
            task = Task(
                project_id=uuid.UUID(project_id), room_id=uuid.UUID(room_id), title="活"
            )
            s.add(task)
            await s.commit()
            ids.append(str(task.id))

    asyncio.run(_add())
    return ids[0]


def _on_task(client, pid, rid, task_id, author, *, ago, author_type, meta):
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(pid),
                    topic_id=uuid.UUID(rid),
                    task_id=uuid.UUID(task_id),
                    kind=BlockKind.message,
                    author_type=author_type,
                    author=author,
                    content="…",
                    created_at=at,
                    meta=meta,
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def test_a_failed_check_on_a_thread_waits_for_an_agent(client):
    pid, rid = _room(client)
    tid = _task(client, pid, rid)
    at = _on_task(
        client,
        pid,
        rid,
        tid,
        "cheese",
        ago=timedelta(minutes=7),
        author_type=AuthorType.platform,
        meta={"event_type": "gate_failed", "severity": "error"},
    )
    since = _since(client, pid, rid)
    assert since is not None
    assert abs(since - at) < timedelta(seconds=1)


def test_an_agent_speaking_in_the_thread_takes_the_check_up(client):
    pid, rid = _room(client)
    tid = _task(client, pid, rid)
    _on_task(
        client,
        pid,
        rid,
        tid,
        "cheese",
        ago=timedelta(minutes=7),
        author_type=AuthorType.platform,
        meta={"event_type": "pr_review", "severity": "info"},
    )
    _on_task(
        client,
        pid,
        rid,
        tid,
        AGENT,
        ago=timedelta(minutes=6),
        author_type=AuthorType.participant,
        meta={},
    )
    assert _since(client, pid, rid) is None


def test_a_notice_that_is_not_for_the_agent_does_not_wait(client):
    """验收卡递上来了是等**人**的事——那是黄灯，不是红灯。"""
    pid, rid = _room(client)
    _say(
        client,
        pid,
        rid,
        "cheese",
        ago=timedelta(minutes=7),
        author_type=AuthorType.platform,
        meta={"event_type": "card_filed", "severity": "info"},
    )
    assert _since(client, pid, rid) is None


# ---- 为什么还没人回：机器 / 环境那一侧 -----------------------------------


def _reason(client, project_id: str, room_id: str) -> str | None:
    rows = client.get(f"/topics?project_id={project_id}").json()["data"]["data"]
    listed = next(t for t in rows if t["id"] == room_id)["reply_wait_reason"]
    header = client.get(f"/topics/{room_id}").json()["data"]["reply_wait_reason"]
    assert listed == header
    return listed


def _machine(client, pid, rid, event_type, *, ago):
    return _say(
        client,
        pid,
        rid,
        "cheese",
        ago=ago,
        author_type=AuthorType.platform,
        meta={"event_type": event_type, "severity": "info"},
    )


def test_a_plain_summons_waits_for_the_agent(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    assert _reason(client, pid, rid) == "mention"


def test_a_machine_event_during_the_wait_explains_it(client):
    pid, rid = _room(client)
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    _machine(client, pid, rid, "machine_provisioning", ago=timedelta(minutes=8))
    assert _reason(client, pid, rid) == "machine_provisioning"
    # 最近的那一条说了算：机器建好了又在等设备回来。
    _machine(client, pid, rid, "device_waiting", ago=timedelta(minutes=4))
    assert _reason(client, pid, rid) == "device_waiting"


def test_a_machine_event_before_the_wait_does_not_explain_it(client):
    """等待开始前机器就已经好了，这次没回话就跟机器无关。"""
    pid, rid = _room(client)
    _machine(client, pid, rid, "sandbox_rebuilt", ago=timedelta(minutes=20))
    _say(client, pid, rid, "alice", ago=timedelta(minutes=9), summons=True)
    assert _reason(client, pid, rid) == "mention"


def test_no_wait_no_reason(client):
    pid, rid = _room(client)
    _machine(client, pid, rid, "environment_repaired", ago=timedelta(minutes=9))
    assert _reason(client, pid, rid) is None
