"""任务支线 —— 数据地基（迁移 c4a7e91b2d05 + `GET /topics/{id}/tasks`）.

一件活以前是一个房间：一整行 `topics`，连带名册、未读游标、归档决策和侧栏那一行。
它之所以是房间只有一个原因——`blocks.topic_id` 是对话唯一的分组键。这里测两件事：
存量的「工作话题行」搬成 `tasks` 行之后对不对得上，以及一个房间能不能一次读出
「所有支线 + 每条支线的对话」。

迁移那两段 SQL 是**从迁移模块导进来的**，不是照抄一份——照抄的话，测试绿了而真正
发布的那段可以是坏的。
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.project.models import Project
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import (
    Topic,
    TopicKind,
    TopicMembership,
    TopicRole,
    TopicStatus,
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


# --- 迁移：存量工作话题行 → tasks 行 -----------------------------------------


def test_backfill_lands_every_shape_of_work_row_in_the_right_room(client):
    """铺出线上真实存在的四种形状，跑一遍迁移自带的 SQL，逐项核对。

    四种形状都来自本项目实测的 219 行：普通 task、**父亲是 task 的嵌套 task**
    （`_child_kind` 从不检查父亲是不是 task）、历史值 `subtopic`、以及从**私聊房间**
    拆出来的 task（私聊不出现在项目话题列表里）。
    """
    tasks_sql, blocks_sql = _backfill_sql()
    seen: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()

            def _topic(key: str, kind: TopicKind, parent: Topic | None, **kw) -> Topic:
                t = Topic(
                    project_id=project.id,
                    title=key,
                    kind=kind,
                    parent_id=parent.id if parent else None,
                    **kw,
                )
                s.add(t)
                return t

            root = _topic("root", TopicKind.root, None)
            await s.flush()
            room = _topic("房间", TopicKind.topic, root)
            private = _topic("私聊", TopicKind.topic, root, is_private=True)
            await s.flush()

            plain = _topic(
                "普通任务",
                TopicKind.task,
                room,
                status=TopicStatus.archived,
                created_by="alice",
                accepted_by="bob",
                accepted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
                archived_at=datetime(2026, 8, 1, 11, tzinfo=UTC),
            )
            pinned_branch = _topic(
                "写死了分支名的任务",
                TopicKind.task,
                room,
                branch_name="explicit/branch",
            )
            legacy = _topic("历史 subtopic", TopicKind.subtopic, room)
            in_private = _topic("私聊里拆的任务", TopicKind.task, private)
            await s.flush()

            nested = _topic("嵌套任务", TopicKind.task, pinned_branch)
            under_legacy = _topic("subtopic 底下的任务", TopicKind.task, legacy)
            await s.flush()

            # 唯一的主今天写在子话题自己的名册里（seed_split）
            s.add(
                TopicMembership(
                    topic_id=plain.id, member_handle="alice", role=TopicRole.owner
                )
            )
            s.add(
                TopicMembership(
                    topic_id=plain.id, member_handle="bob", role=TopicRole.member
                )
            )
            await s.flush()

            for name, topic in {
                "room": room,
                "private": private,
                "plain": plain,
                "pinned_branch": pinned_branch,
                "legacy": legacy,
                "in_private": in_private,
                "nested": nested,
                "under_legacy": under_legacy,
            }.items():
                seen[name] = topic.id

            await s.execute(text(tasks_sql))
            await s.execute(text(blocks_sql))
            await s.commit()

    client.portal.call(_seed)

    rows: dict[uuid.UUID, Task] = {}

    async def _read() -> None:
        async with client.test_factory() as s:
            for tid in seen.values():
                task = await s.get(Task, tid)
                if task is not None:
                    rows[tid] = task

    client.portal.call(_read)

    # 六行工作话题 → 六行 task，房间自己不变成 task
    assert set(rows) == {
        seen[k]
        for k in (
            "plain",
            "pinned_branch",
            "legacy",
            "in_private",
            "nested",
            "under_legacy",
        )
    }

    plain = rows[seen["plain"]]
    assert plain.room_id == seen["room"]
    assert plain.status is TaskStatus.closed
    assert plain.owner_handle == "alice"  # 从名册里的 owner 来，不是 member
    assert plain.created_by == "alice"
    assert (plain.accepted_by, plain.accepted_at.isoformat()) == (
        "bob",
        datetime(2026, 8, 1, 10, tzinfo=UTC).isoformat(),
    )
    assert plain.closed_at == datetime(2026, 8, 1, 11, tzinfo=UTC)

    # 嵌套的活归到它头上那个真正的房间，而不是它的父任务
    assert rows[seen["nested"]].room_id == seen["room"]
    assert rows[seen["under_legacy"]].room_id == seen["room"]
    # 私聊也是房间
    assert rows[seen["in_private"]].room_id == seen["private"]
    # 没有名册的活，主就是空的——不硬造一个
    assert rows[seen["nested"]].owner_handle is None
    # 没交付过的活不许被 status 冒充成交付
    assert rows[seen["nested"]].accepted_at is None
    assert rows[seen["legacy"]].status is TaskStatus.open


def test_backfill_stores_the_branch_the_code_actually_uses(client):
    """`topics.branch_name` 全库没有写入方，所以逐字复制会搬过去一堆 NULL。

    工作区真正落在的分支是派生出来的（`branch_for_place` → `topic/<前 8 位 hex>`），
    迁移存的必须是那个值；而万一哪一行真的写了显式分支名，显式的那个说了算。
    """
    tasks_sql, _ = _backfill_sql()
    ids: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            derived = Topic(
                project_id=project.id,
                title="派生分支",
                kind=TopicKind.task,
                parent_id=room.id,
            )
            explicit = Topic(
                project_id=project.id,
                title="显式分支",
                kind=TopicKind.task,
                parent_id=room.id,
                branch_name="explicit/branch",
            )
            s.add_all([derived, explicit])
            await s.flush()
            ids["derived"] = derived.id
            ids["explicit"] = explicit.id
            await s.execute(text(tasks_sql))
            await s.commit()

    client.portal.call(_seed)

    branches: dict[str, str | None] = {}

    async def _read() -> None:
        async with client.test_factory() as s:
            for key, tid in ids.items():
                task = await s.get(Task, tid)
                assert task is not None
                branches[key] = task.branch_name

    client.portal.call(_read)

    from app.domain.workspace.service import branch_for_place

    assert branches["derived"] == branch_for_place(ids["derived"])
    assert branches["explicit"] == "explicit/branch"


def test_backfill_keys_a_work_topics_blocks_onto_its_thread(client):
    """工作话题里的每一条 block（含它那份实况文档）都拿到支线键；房间自己那条线不动。"""
    tasks_sql, blocks_sql = _backfill_sql()
    ids: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            work = Topic(
                project_id=project.id,
                title="一件活",
                kind=TopicKind.task,
                parent_id=room.id,
            )
            s.add(work)
            await s.flush()

            def _block(topic: Topic, kind: BlockKind, content: str) -> Block:
                b = Block(
                    project_id=project.id,
                    topic_id=topic.id,
                    kind=kind,
                    author_type=AuthorType.human,
                    author="alice",
                    content=content,
                )
                s.add(b)
                return b

            room_line = _block(room, BlockKind.message, "房间自己说的话")
            brief = _block(work, BlockKind.doc, "任务简报")
            said = _block(work, BlockKind.message, "支线里说的话")
            await s.flush()
            ids.update(
                room_line=room_line.id, brief=brief.id, said=said.id, work=work.id
            )
            await s.execute(text(tasks_sql))
            await s.execute(text(blocks_sql))
            await s.commit()

    client.portal.call(_seed)

    keys: dict[str, uuid.UUID | None] = {}

    async def _read() -> None:
        async with client.test_factory() as s:
            for name in ("room_line", "brief", "said"):
                block = await s.get(Block, ids[name])
                assert block is not None
                keys[name] = block.task_id

    client.portal.call(_read)

    assert keys["brief"] == ids["work"]
    assert keys["said"] == ids["work"]
    # 房间自己那条线上的消息不属于任何支线
    assert keys["room_line"] is None


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

            talkative = Task(
                project_id=project.id,
                room_id=room.id,
                title="聊得多的活",
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            silent = Task(
                project_id=project.id,
                room_id=room.id,
                title="没人说话的活",
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
            elsewhere = Task(
                project_id=project.id, room_id=other.id, title="别的房间的活"
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
