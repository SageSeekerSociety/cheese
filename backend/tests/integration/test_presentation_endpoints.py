"""四条读接口都带上看板那一格，而且带的是同一句话。

单测钉的是「从事实算出哪一格」；这里钉的是另一件事：**每条读接口都真的把它算了
出来，而且同一条活在四个地方读到的是同一格**。这两件事会各自坏掉——派生对了但某
条路由忘了填，或者某条路由自己又算了一遍——而只有第二种会让屏幕上出现两种说法。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task, TaskProgress, TaskStatus
from app.domain.room_task.presentation import Building, Done, NeedsYou
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team


def _seeded(client) -> dict[str, str]:
    """一个房间，四条活：队友说在做的、队友什么都没说的、等人验收的、关掉时留下了
    结论的。"""
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

            def _task(title: str, **closed) -> Task:
                return Task(
                    project_id=project.id,
                    room_id=room.id,
                    title=title,
                    owner_handle="alice",
                    **closed,
                )

            running = _task(
                "队友说在做的活",
                progress_state=TaskProgress.working,
                progress_set_at=now,
                progress_set_by="cheese",
            )
            started = _task("停着的活")
            waiting = _task("等人验收的活")
            concluded = _task(
                "有结论的活",
                status=TaskStatus.closed,
                conclusion="结论是不做迁移",
                closed_at=now,
            )
            s.add_all([running, started, waiting, concluded])
            await s.flush()

            s.add(
                AcceptCard(
                    topic_id=room.id,
                    task_id=waiting.id,
                    status=AcceptStatus.pending,
                )
            )
            await s.flush()

            ids.update(
                project=str(project.id),
                room=str(room.id),
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

    assert by_id[ids["running"]]["presentation"] == {
        "column": "building",
        "phrase": Building.running,
    }
    # 队友什么都没说过，也还没递出交付：「做了一半停着」。
    assert by_id[ids["started"]]["presentation"] == {
        "column": "building",
        "phrase": Building.started,
    }
    # 安静，但等的是人 —— 和「停着」意味着相反的下一步（去验收 vs 去催）。
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
    """A task in the room is being worked on; the room's own cell on the board is
    still the room's: the task has a cell of its own."""
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


def _the_rooms_session_declares(client, room: str, status: str) -> None:
    """The runner of the room's own line says its session is ``status``."""
    from app.domain.agent import session_state

    key = session_state.SeatKey(uuid.UUID(room), "cheese", "claude-code")

    async def _declare() -> None:
        await session_state.register(client.test_factory, key)
        async with client.test_factory() as s:
            await session_state.declare(s, key, status)
            await s.commit()

    client.portal.call(_declare)


def test_a_rooms_board_cell_follows_its_session_as_its_runner_declares_it(client):
    """「运行中」 is what the runner of the room's own line says of its session,
    on the room's header and in the room list alike; no turn is in flight in
    this process for it either way."""
    ids = _seeded(client)

    def cells() -> tuple[str, str]:
        header = client.get(f"/topics/{ids['room']}").json()["data"]
        listed = client.get(f"/topics?project_id={ids['project']}").json()["data"]
        row = next(t for t in listed["data"] if t["id"] == ids["room"])
        return header["presentation"]["phrase"], row["presentation"]["phrase"]

    _the_rooms_session_declares(client, ids["room"], "running")
    assert cells() == (Building.running, Building.running)

    _the_rooms_session_declares(client, ids["room"], "idle")
    assert cells() == (Building.idle, Building.idle)


def test_a_member_is_working_in_a_room_while_its_runner_says_its_session_runs(
    client,
):
    """Who is at work in a room is what its sessions' runners declare, on the
    room's header and in the room list alike, and in no other process's turn
    books: no turn runs here for it."""
    ids = _seeded(client)
    seat = "cheese-0123456789ab"
    from app.domain.agent import session_state

    key = session_state.SeatKey(uuid.UUID(ids["room"]), seat, "claude-code")

    def declares(status: str) -> None:
        async def _declare() -> None:
            await session_state.register(client.test_factory, key)
            async with client.test_factory() as s:
                await session_state.declare(s, key, status)
                await s.commit()

        client.portal.call(_declare)

    def working() -> tuple[list[str], list[str]]:
        header = client.get(f"/topics/{ids['room']}").json()["data"]
        listed = client.get(f"/topics?project_id={ids['project']}").json()["data"]
        row = next(t for t in listed["data"] if t["id"] == ids["room"])
        return (
            [a["member"] for a in header["activity"] if a["kind"] == "working"],
            [a["member"] for a in row["activity"] if a["kind"] == "working"],
        )

    declares("running")
    assert working() == ([seat], [seat])
    declares("idle")
    assert working() == ([], [])
