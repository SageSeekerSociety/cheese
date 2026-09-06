"""四条读接口都带上看板那一格，而且带的是同一句话。

单测钉的是「从事实算出哪一格」；这里钉的是另一件事：**每条读接口都真的把它算了
出来，而且同一条活在四个地方读到的是同一格**。这两件事会各自坏掉——派生对了但某
条路由忘了填，或者某条路由自己又算了一遍——而只有第二种会让屏幕上出现两种说法。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task, WorkTree
from app.domain.room_task.presentation import LOST_SIGNAL_AFTER
from app.domain.topic.models import Topic, TopicKind


def _seeded(client, stub_hooks=None) -> dict[str, str]:
    """一个房间，四条活：分身在做的、等人验收的、有分身但早就没动静的、还在说话的。

    传了 `stub_hooks` 就顺带把这个房间的屏幕点亮 —— 分身住在房间的会话里，房间的
    屏幕没了它一定也没了，所以「有分身在做」这一格只有屏幕活着时才成立。
    """
    ids: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            tree = WorkTree(project_id=project.id, room_id=room.id)
            s.add(tree)
            await s.flush()

            running = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="在跑的活",
                subagent_id="agent-running",
                last_turn_at=datetime.now(UTC),
            )
            waiting = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="等人验收的活",
            )
            lost = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="失联的活",
                subagent_id="agent-lost",
                last_turn_at=datetime.now(UTC) - LOST_SIGNAL_AFTER - timedelta(hours=1),
            )
            # 有分身、认领时间也早就过期了，但它刚说过话 —— block 才是心跳，
            # last_turn_at 只在认领那一刻盖一次。
            talking = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="还在说话的活",
                subagent_id="agent-talking",
                last_turn_at=datetime.now(UTC) - LOST_SIGNAL_AFTER - timedelta(hours=1),
            )
            s.add_all([running, waiting, lost, talking])
            await s.flush()

            s.add(
                Block(
                    project_id=project.id,
                    topic_id=room.id,
                    task_id=talking.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.ai,
                    author="cheese",
                    content="刚说的",
                )
            )
            await s.flush()

            s.add(
                AcceptCard(
                    topic_id=room.id,
                    task_id=waiting.id,
                    tree_id=tree.id,
                    reviewer_handle="alice",
                    status=AcceptStatus.pending,
                )
            )
            await s.flush()

            ids.update(
                project=str(project.id),
                room=str(room.id),
                running=str(running.id),
                waiting=str(waiting.id),
                lost=str(lost.id),
                talking=str(talking.id),
            )
            await s.commit()

    asyncio.run(_seed())
    if stub_hooks is not None:
        stub_hooks.runtime._live[uuid.UUID(ids["room"])] = "screen"
    return ids


def test_the_project_task_list_carries_the_board_cell(client, stub_hooks):
    ids = _seeded(client, stub_hooks)
    rows = client.get(f"/projects/{ids['project']}/tasks").json()["data"]["data"]
    by_id = {r["id"]: r for r in rows}

    assert by_id[ids["running"]]["presentation"] == {
        "column": "building",
        "display_status": "运行中",
    }
    # 安静，但等的是人 —— 光看 open/closed 和「空闲」一模一样，而这两者意味着相反的
    # 下一步（去验收 vs 去催）。
    assert by_id[ids["waiting"]]["presentation"] == {
        "column": "needs_you",
        "display_status": "等待验收",
    }
    # 说有分身在做，但没有任何东西最近确认过 —— 今天前端没有这一格。
    assert by_id[ids["lost"]]["presentation"] == {
        "column": "building",
        "display_status": "失联",
    }
    # 同样一个过期的 last_turn_at，但它刚落了一个 block：心跳压过认领时间。
    assert by_id[ids["talking"]]["presentation"] == {
        "column": "building",
        "display_status": "运行中",
    }


def test_a_room_and_its_threads_agree_with_the_project_list(client, stub_hooks):
    ids = _seeded(client, stub_hooks)
    project_rows = client.get(f"/projects/{ids['project']}/tasks").json()["data"][
        "data"
    ]
    from_project = {r["id"]: r["presentation"] for r in project_rows}

    room_rows = client.get(f"/topics/{ids['room']}/tasks").json()["data"]["data"]
    from_room = {r["id"]: r["presentation"] for r in room_rows}

    from_header = {
        task_id: client.get(f"/topics/{task_id}").json()["data"]["presentation"]
        for task_id in (
            ids["running"],
            ids["waiting"],
            ids["lost"],
            ids["talking"],
        )
    }

    assert from_room == from_project
    assert from_header == from_project


def test_a_room_carries_its_own_board_cell(client):
    ids = _seeded(client)
    # 房间自己没有卡（那张 pending 卡是一条活的），所以房间是空闲的 —— 一条活在等
    # 验收，不能让它上面那个房间也显示成等验收。
    header = client.get(f"/topics/{ids['room']}").json()["data"]
    assert header["presentation"] == {"column": "building", "display_status": "空闲"}

    listed = client.get(f"/topics?project_id={ids['project']}").json()["data"]["data"]
    rooms = {t["id"]: t["presentation"] for t in listed}
    assert rooms[ids["room"]] == header["presentation"]


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
        "display_status": "等待验收",
    }
