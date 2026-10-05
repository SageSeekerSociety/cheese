"""四条读接口都带上看板那一格，而且带的是同一句话。

单测钉的是「从事实算出哪一格」；这里钉的是另一件事：**每条读接口都真的把它算了
出来，而且同一条活在四个地方读到的是同一格**。这两件事会各自坏掉——派生对了但某
条路由忘了填，或者某条路由自己又算了一遍——而只有第二种会让屏幕上出现两种说法。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.agent.models import AgentTurn
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.presentation import Building, Done, NeedsYou, NotStarted
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team


def _seeded(client) -> dict[str, str]:
    """一个房间，五条活：还在讨论的、开始了正在跑一轮的、开始了此刻没在跑的、
    等人验收的、关掉时留下了结论的。"""
    ids: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(team_id=await a_team(s), name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()

            now = datetime.now(UTC)

            def _task(title: str, **started) -> Task:
                return Task(
                    project_id=project.id,
                    room_id=room.id,
                    title=title,
                    owner_handle="alice",
                    **started,
                )

            begun = {"started_at": now, "started_by": "alice"}
            discussing = _task("还在讨论的活")
            running = _task("在跑的活", **begun)
            started = _task("开始了的活", **begun)
            waiting = _task("等人验收的活", **begun)
            concluded = _task(
                "有结论的活",
                **begun,
                status=TaskStatus.closed,
                conclusion="结论是不做迁移",
                closed_at=now,
            )
            s.add_all([discussing, running, started, waiting, concluded])
            await s.flush()

            # 任务自己的会话里有一轮送达了、还没停 —— 这就是「在跑」。
            s.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    topic_id=room.id,
                    task_id=running.id,
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    content="开始吧",
                    started_at=now,
                    delivered_at=now,
                )
            )
            s.add(
                AcceptCard(
                    topic_id=room.id,
                    task_id=waiting.id,
                    reviewer_handle="alice",
                    status=AcceptStatus.pending,
                )
            )
            await s.flush()

            ids.update(
                project=str(project.id),
                room=str(room.id),
                discussing=str(discussing.id),
                running=str(running.id),
                started=str(started.id),
                waiting=str(waiting.id),
                concluded=str(concluded.id),
            )
            await s.commit()

    asyncio.run(_seed())
    return ids


def test_the_project_task_list_carries_the_board_cell(client):
    ids = _seeded(client)
    rows = client.get(f"/projects/{ids['project']}/tasks").json()["data"]["data"]
    by_id = {r["id"]: r for r in rows}

    # 负责人还没点「开始」—— 它还在讨论，不在施工。
    assert by_id[ids["discussing"]]["presentation"] == {
        "column": "not_started",
        "phrase": NotStarted.discussing,
    }
    assert by_id[ids["running"]]["presentation"] == {
        "column": "building",
        "phrase": Building.running,
    }
    # 开始了，此刻没有一轮在跑，也还没递出交付：「做了一半停着」，不是「还没开始」。
    assert by_id[ids["started"]]["presentation"] == {
        "column": "building",
        "phrase": Building.started,
    }
    # 安静，但等的是人 —— 和「开始了没在跑」意味着相反的下一步（去验收 vs 去催）。
    assert by_id[ids["waiting"]]["presentation"] == {
        "column": "needs_you",
        "phrase": NeedsYou.awaiting_review,
    }
    assert by_id[ids["concluded"]]["presentation"] == {
        "column": "done",
        "phrase": Done.completed,
    }


def test_a_room_and_its_tasks_agree_with_the_project_list(client):
    ids = _seeded(client)
    project_rows = client.get(f"/projects/{ids['project']}/tasks").json()["data"][
        "data"
    ]
    from_project = {r["id"]: r["presentation"] for r in project_rows}

    room_rows = client.get(f"/topics/{ids['room']}/tasks").json()["data"]["data"]
    from_room = {r["id"]: r["presentation"] for r in room_rows}

    # 单开一条活看到的那一格，和它在两份清单里显示的必须是同一句话 —— 同一个函数
    # 算的，所以深链接进来和从看板点进来不可能给出两种说法。
    from_card = {
        task_id: client.get(f"/topics/{task_id}/task").json()["data"]["presentation"]
        for task_id in (
            ids["discussing"],
            ids["running"],
            ids["started"],
            ids["waiting"],
            ids["concluded"],
        )
    }

    assert from_room == from_project
    assert from_card == from_project


def test_a_room_carries_its_own_board_cell(client):
    ids = _seeded(client)
    # 房间自己没有卡（那张 pending 卡是一条活的），所以房间是空闲的 —— 一条活在等
    # 验收，不能让它上面那个房间也显示成等验收。
    header = client.get(f"/topics/{ids['room']}").json()["data"]
    assert header["presentation"] == {
        "column": "building",
        "phrase": Building.idle,
    }

    listed = client.get(f"/topics?project_id={ids['project']}").json()["data"]["data"]
    rooms = {t["id"]: t["presentation"] for t in listed}
    assert rooms[ids["room"]] == header["presentation"]


def test_a_rooms_board_cell_does_not_follow_a_task_running_under_it(client):
    """A task in the room is running; the room's own cell on the board is still
    the room's: the task has a cell of its own."""
    ids = _seeded(client)

    listed = client.get(f"/topics?project_id={ids['project']}").json()["data"]["data"]
    row = next(t for t in listed if t["id"] == ids["room"])
    assert row["presentation"]["phrase"] == Building.idle


def test_a_rooms_own_card_reaches_the_room(client):
    ids = _seeded(client)

    async def _file() -> None:
        async with client.test_factory() as s:
            # 房间自己那张卡：`task_id` 是 NULL，这正是它和一条活的卡的区别。
            s.add(
                AcceptCard(
                    topic_id=uuid.UUID(ids["room"]),
                    task_id=None,
                    reviewer_handle="alice",
                    status=AcceptStatus.pending,
                )
            )
            await s.commit()

    asyncio.run(_file())

    header = client.get(f"/topics/{ids['room']}").json()["data"]
    assert header["presentation"] == {
        "column": "needs_you",
        "phrase": NeedsYou.awaiting_review,
    }
