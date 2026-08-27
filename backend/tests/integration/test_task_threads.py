"""一个房间的全部支线，每条带自己的对话.

一件活以前是一个房间：一整行 `topics`，连带名册、未读游标、归档决策和侧栏那一行。
它之所以是房间只有一个原因——`blocks.topic_id` 是对话唯一的分组键。现在
`blocks.task_id` 是那个键，一条支线便宜到不用是房间。

这里测的是读路径：一个房间能不能一次读出「所有支线 + 每条支线的对话」，包括
一句话都没说过的那条（存在但没人说话是个真实答案，不能被丢掉）。
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import MAX_RESIDENT_TASKS_PER_ROOM, Task, WorkTree
from app.domain.room_task.repositories import TaskRepository
from app.domain.room_task.services import ResidencyService
from app.domain.topic.models import (
    Topic,
    TopicKind,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c4a7e91b2d05_a_task_is_a_thread_in_a_room.py"
)


def _backfill_sql() -> tuple[str, str]:
    spec = importlib.util.spec_from_file_location("_task_threads_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BACKFILL_TASKS, module.BACKFILL_BLOCKS


# --- 读路径：一个房间的全部支线 ----------------------------------------------


def _room_with_threads(client) -> dict:
    """一个房间：两条支线（一条有两句话，一条一句没说），外加房间自己的一条线。"""
    ids: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            other = Topic(project_id=project.id, title="别的房间", kind=TopicKind.topic)
            s.add_all([room, other])
            await s.flush()

            # 一批活共用一棵树. Each room takes its work into one.
            tree = WorkTree(project_id=project.id, room_id=room.id)
            other_tree = WorkTree(project_id=project.id, room_id=other.id)
            s.add_all([tree, other_tree])
            await s.flush()

            talkative = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="聊得多的活",
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            silent = Task(
                project_id=project.id,
                room_id=room.id,
                tree_id=tree.id,
                title="没人说话的活",
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
            elsewhere = Task(
                project_id=project.id,
                room_id=other.id,
                tree_id=other_tree.id,
                title="别的房间的活",
            )
            s.add_all([talkative, silent, elsewhere])
            await s.flush()

            def _block(task: Task | None, kind: BlockKind, content: str, at) -> Block:
                b = Block(
                    project_id=project.id,
                    topic_id=room.id,
                    task_id=task.id if task else None,
                    kind=kind,
                    author_type=AuthorType.human,
                    author="alice",
                    content=content,
                    created_at=at,
                )
                s.add(b)
                return b

            _block(
                None,
                BlockKind.message,
                "房间自己的线",
                datetime(2026, 8, 1, 1, tzinfo=UTC),
            )
            _block(
                talkative,
                BlockKind.message,
                "第一句",
                datetime(2026, 8, 1, 2, tzinfo=UTC),
            )
            _block(
                talkative,
                BlockKind.message,
                "第二句",
                datetime(2026, 8, 1, 3, tzinfo=UTC),
            )
            _block(
                talkative,
                BlockKind.message,
                "第三句",
                datetime(2026, 8, 1, 4, tzinfo=UTC),
            )
            # 文档视图的东西不进对话
            _block(
                talkative,
                BlockKind.comment,
                "段落评论",
                datetime(2026, 8, 1, 5, tzinfo=UTC),
            )
            _block(
                elsewhere,
                BlockKind.message,
                "隔壁房间说的",
                datetime(2026, 8, 1, 6, tzinfo=UTC),
            )
            await s.flush()
            ids.update(
                room=room.id,
                other=other.id,
                talkative=talkative.id,
                silent=silent.id,
                elsewhere=elsewhere.id,
            )
            await s.commit()

    client.portal.call(_seed)
    return ids


def _threads(client, room_id, **params) -> list[dict]:
    r = client.get(f"/topics/{room_id}/tasks", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_a_room_reads_back_every_thread_with_its_own_conversation(client):
    ids = _room_with_threads(client)
    threads = _threads(client, ids["room"])

    assert [t["title"] for t in threads] == ["聊得多的活", "没人说话的活"]
    talkative, silent = threads
    assert [b["content"] for b in talkative["blocks"]] == ["第一句", "第二句", "第三句"]
    # 一句话没说的活照样是一条支线——不能因为没人开口就从房间里消失
    assert silent["blocks"] == []
    # 房间自己那条线不在这里，它在 /blocks
    assert all("房间自己的线" not in b["content"] for b in talkative["blocks"])
    # 隔壁房间的活也不在
    assert ids["elsewhere"] not in {uuid.UUID(t["id"]) for t in threads}


def test_limit_caps_each_thread_rather_than_the_room(client):
    """limit 是每条支线各自的窗口。

    如果它卡的是整个房间，一条话多的支线就会把别的支线的开场白挤掉——房间里
    有几条活会取决于哪条活话多，这不是「这个房间有哪些活」的答案。
    """
    ids = _room_with_threads(client)
    threads = _threads(client, ids["room"], limit=2)

    assert [t["title"] for t in threads] == ["聊得多的活", "没人说话的活"]
    assert [b["content"] for b in threads[0]["blocks"]] == ["第二句", "第三句"]
    assert threads[1]["blocks"] == []


def test_no_limit_returns_every_thread_whole(client):
    """跟 /blocks 一样：不传 limit 就是全量，不做静默截断。"""
    ids = _room_with_threads(client)
    assert len(_threads(client, ids["room"])[0]["blocks"]) == 3


def test_an_unknown_room_is_a_404(client):
    r = client.get(f"/topics/{uuid.uuid4()}/tasks")
    assert r.status_code == 404


# --- 房间总览要的那三样：在跑没在跑、排没排队、等不等验收 ----------------------
#
# 「现在有什么在动」是打开一个房间的第一个问题，而 open/closed 答不了它：四条
# 都开着的房间，可能三条在跑一条排队，也可能全都闲着。这三条测的就是这一格资料
# 从 API 出得来 —— 出不来的话，界面上「在跑」和「闲着」长得一模一样。


def test_a_thread_says_whether_it_is_holding_a_slot(client):
    """在跑 / 闲着，是两个不同的答案，而且都不等于 open。"""
    ids = _room_with_threads(client)

    async def _start_one() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            await svc.touch(await TaskRepository(s).get(ids["talkative"]))
            await s.commit()

    client.portal.call(_start_one)

    by_title = {t["title"]: t for t in _threads(client, ids["room"], limit=1)}
    assert by_title["聊得多的活"]["residency"] == "running"
    assert by_title["没人说话的活"]["residency"] == "idle"
    # 两条都还开着 —— 所以 status 分不出它们，这正是 residency 存在的理由。
    assert {t["status"] for t in by_title.values()} == {"open"}


def test_a_queued_thread_says_since_when_it_has_been_waiting(client):
    """排队中要能和「闲着」分开：闲着是没人找它，排队是它想跑但房间满了。

    等了多久是排队这件事唯一有用的附加信息 —— 一个房间满了四条，谁下一个上要
    看谁等得久，界面上没有这个时间就只能说一句「排队中」。
    """
    ids = _room_with_threads(client)

    async def _fill_then_queue() -> None:
        async with client.test_factory() as s:
            svc = ResidencyService(s)
            repo = TaskRepository(s)
            # 把房间占满，再让已有的那条去排队。
            for i in range(MAX_RESIDENT_TASKS_PER_ROOM):
                filler = Task(
                    project_id=(await repo.get(ids["talkative"])).project_id,
                    room_id=ids["room"],
                    tree_id=(await repo.get(ids["talkative"])).tree_id,
                    title=f"占位 {i}",
                )
                s.add(filler)
                await s.flush()
                await svc.admit(filler)
            assert await svc.admit(await repo.get(ids["silent"])) is False
            await s.commit()

    client.portal.call(_fill_then_queue)

    queued = next(
        t
        for t in _threads(client, ids["room"], limit=1)
        if t["title"] == "没人说话的活"
    )
    assert queued["residency"] == "idle", "排队的没有占着槽位"
    assert queued["queued_at"] is not None, "排队中和闲着必须分得开"


def test_a_thread_carries_the_card_it_is_riding_on(client):
    """等人验收也是安静的 —— 光看 residency 和「闲着」一模一样。"""
    ids = _room_with_threads(client)

    async def _file_a_card() -> None:
        async with client.test_factory() as s:
            task = await TaskRepository(s).get(ids["talkative"])
            s.add(
                AcceptCard(
                    topic_id=ids["room"],
                    task_id=task.id,
                    tree_id=task.tree_id,
                    reviewer_handle="alice",
                    routing_reason="最懂",
                    status=AcceptStatus.pending,
                )
            )
            await s.commit()

    client.portal.call(_file_a_card)

    by_title = {t["title"]: t for t in _threads(client, ids["room"], limit=1)}
    assert by_title["聊得多的活"]["card"]["status"] == "pending"
    # 没递过卡的那条是 None，不是缺字段 —— 界面靠它区分「没递」和「递了在等」。
    assert by_title["没人说话的活"]["card"] is None
