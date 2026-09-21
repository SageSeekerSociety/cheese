"""教师看板 · 学习这一格：谁能看、卡在哪、提纲从哪几条拼出来。

这一格读的是对话，所以有一套自己的判据：每个项目逐条过 ``may_read_project``。
测试把「课程 → 项目」那条路和那道门都换成假的（``_service``），剩下被测的是这一格
自己的逻辑：分组、计数、排序、提纲、以及那条没有数据源的队列如实报缺。

假 session 不判 SQL 的 WHERE，它照着语句里绑进去的参数把集合再筛一遍 —— 真能筛的
（项目、房间、分类、时间）都筛，筛不了的判据（署名前缀、私聊、消息类型）另有一组
编译 SQL 的测试盯着（``TestStudentQuestionQuery``）。
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.space import learning_service
from app.domain.space.learning_service import (
    QUESTION_LIMIT,
    REVIEW_FLAG_MISSING,
    SpaceLearningService,
    _excerpt,
    _from_ms,
    _to_ms,
)

NOW = datetime(2026, 3, 10, 9, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 3, 11, 9, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 假的读库
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def _table_key(stmt) -> str:
    """这条语句在读谁 —— 按选中的实体分流。"""
    for description in stmt.column_descriptions:
        entity = description.get("entity")
        if entity is not None:
            return entity.__name__
        table = getattr(description.get("expr"), "table", None)
        if table is not None:
            return table.name
    return ""


def _bound(stmt) -> tuple[set, set, list]:
    """语句绑进去的参数（uuid / 字符串 / 时间）。

    假 session 用它们把 ``WHERE ... IN (...)`` 和 ``BETWEEN`` 照着做一遍，好让
    测试里的筛选和真库里的筛选是同一件事。
    """
    uuids: set[uuid.UUID] = set()
    strings: set[str] = set()
    moments: list[datetime] = []
    for value in stmt.compile().params.values():
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            if isinstance(item, uuid.UUID):
                uuids.add(item)
            elif isinstance(item, str):
                strings.add(item)
            elif isinstance(item, datetime):
                moments.append(item)
    return uuids, strings, moments


class FakeSession:
    """只认这一格会发的几条语句；别的语句一律算「没写过这条」。"""

    def __init__(
        self,
        *,
        projects=(),
        blocks=(),
        topics=(),
        tasks=(),
        categories=(),
        users=(),
        space_name="程序设计基础",
    ):
        self.projects = list(projects)
        self.blocks = list(blocks)
        self.topics = list(topics)
        self.tasks = list(tasks)
        self.categories = list(categories)
        self.users = list(users)
        self.space_name = space_name

    async def execute(self, stmt):
        uuids, strings, moments = _bound(stmt)
        key = _table_key(stmt)
        if key == "Block":
            rows = [
                b
                for b in self.blocks
                if (not uuids or b.id in uuids or b.project_id in uuids)
                and self._within(b.created_at, moments)
            ]
            rows.sort(key=lambda b: b.created_at, reverse=True)
            return _FakeResult(rows[:QUESTION_LIMIT])
        if key == "Project":
            return _FakeResult([p for p in self.projects if not uuids or p.id in uuids])
        if key == "Topic":
            return _FakeResult(
                [(t.id, t.title) for t in self.topics if not uuids or t.id in uuids]
            )
        if key == "Task":
            return _FakeResult(
                [
                    (t.id, t.category_id)
                    for t in self.tasks
                    if not uuids or t.id in uuids
                ]
            )
        if key == "SpaceCategory":
            space_id = self.space_id_hint(stmt)
            return _FakeResult(
                [
                    (c.id, c.name)
                    for c in self.categories
                    if (not uuids or c.id in uuids)
                    and (space_id is None or c.space_id == space_id)
                ]
            )
        if key == "User":
            return _FakeResult(
                [
                    (u.username, u.nickname)
                    for u in self.users
                    if not strings or u.username in strings
                ]
            )
        raise AssertionError(f"unexpected statement: {key}")

    async def scalar(self, stmt):
        assert _table_key(stmt) == "Space"
        return self.space_name

    @staticmethod
    def space_id_hint(stmt) -> int | None:
        for value in stmt.compile().params.values():
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _within(moment: datetime, moments: list[datetime]) -> bool:
        if not moments:
            return True
        return min(moments) <= moment <= max(moments)


# ---------------------------------------------------------------------------
# 造数据
# ---------------------------------------------------------------------------


def project(name: str, owner: str, task_id: int | None):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, owner_handle=owner, external_task_id=task_id
    )


def block(
    proj,
    *,
    author: str | None = None,
    content: str = "这一步为什么要先乘后加？",
    created_at: datetime = NOW,
    topic_id: uuid.UUID | None = None,
    block_id: uuid.UUID | None = None,
):
    return SimpleNamespace(
        id=block_id or uuid.uuid4(),
        project_id=proj.id,
        topic_id=topic_id or uuid.uuid4(),
        author=author or proj.owner_handle,
        content=content,
        created_at=created_at,
    )


def topic(title: str = "第五次作业"):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


def category(category_id: int, name: str, space_id: int = 7):
    return SimpleNamespace(id=category_id, name=name, space_id=space_id)


def task(task_id: int, category_id: int):
    return SimpleNamespace(id=task_id, category_id=category_id)


def user(username: str, nickname: str | None):
    return SimpleNamespace(username=username, nickname=nickname)


def _service(
    monkeypatch,
    session: FakeSession,
    *,
    project_ids: list[uuid.UUID] | None = None,
    readable: list[uuid.UUID] | None = None,
) -> SpaceLearningService:
    """把「课程 → 项目」那条路和逐项目的门都换成假的。

    ``readable`` 省掉就是全都读得了；给了就是「只有这些读得了」—— 页面上的学生
    名单、队列、提纲三处都该跟着它一起缩。
    """
    ids = list(
        project_ids if project_ids is not None else [p.id for p in session.projects]
    )
    allowed = set(ids if readable is None else readable)

    class DummyProjectRepository:
        def __init__(self, _session):
            pass

        async def list_ids_for_space_tasks(self, _space_id):
            return ids

    async def _may_read_project(_session, *, project_id, handle):
        return project_id in allowed

    monkeypatch.setattr(learning_service, "ProjectRepository", DummyProjectRepository)
    monkeypatch.setattr(learning_service, "may_read_project", _may_read_project)
    return SpaceLearningService(session=session)


# ---------------------------------------------------------------------------
# 谁能看什么
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_filters_lists_only_students_the_asker_may_read(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    carol = project("Carol 的作业", "carol", 3)
    session = FakeSession(
        projects=[alice, bob, carol],
        users=[user("alice", "爱丽丝"), user("bob", None), user("carol", "卡罗")],
    )
    svc = _service(monkeypatch, session, readable=[alice.id, bob.id])

    data = await svc.filters(space_id=7, handle="teacher")

    assert [s["handle"] for s in data["students"]] == ["alice", "bob"]
    assert data["projectCount"] == 2


@pytest.mark.anyio
async def test_filters_falls_back_to_the_handle_when_there_is_no_nickname(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    session = FakeSession(
        projects=[alice, bob], users=[user("alice", "爱丽丝"), user("bob", None)]
    )
    svc = _service(monkeypatch, session)

    data = await svc.filters(space_id=7, handle="teacher")

    names = {s["handle"]: s["name"] for s in data["students"]}
    assert names == {"alice": "爱丽丝", "bob": "bob"}


@pytest.mark.anyio
async def test_filters_are_empty_when_no_project_is_readable(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    session = FakeSession(projects=[alice], users=[user("alice", "爱丽丝")])
    svc = _service(monkeypatch, session, readable=[])

    data = await svc.filters(space_id=7, handle=None)

    assert data["students"] == []
    assert data["projectCount"] == 0


@pytest.mark.anyio
async def test_filters_offer_the_courses_own_categories_as_knowledge_points(
    monkeypatch,
):
    """知识点今天读的是课程分类 —— 分类本身就是课程设计的格子，不限已有项目。"""
    session = FakeSession(
        projects=[project("Alice 的作业", "alice", 1)],
        users=[user("alice", "爱丽丝")],
        categories=[category(11, "循环"), category(12, "递归")],
    )
    svc = _service(monkeypatch, session)

    data = await svc.filters(space_id=7, handle="teacher")

    assert {kp["name"] for kp in data["knowledgePoints"]} == {"循环", "递归"}


# ---------------------------------------------------------------------------
# 两个队列
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_queues_report_the_review_flag_signal_as_absent(monkeypatch):
    """没有落库的那一维如实报缺 —— 空队列和一个坏掉的队列必须分得开。"""
    session = FakeSession()
    svc = _service(monkeypatch, session, project_ids=[])

    data = await svc.queues(
        space_id=7, handle="teacher", student=None, from_ts=None, to_ts=None
    )

    assert data["reviewFlag"]["available"] is False
    assert data["reviewFlag"]["reason"] == REVIEW_FLAG_MISSING
    assert data["reviewFlag"]["items"] == []


@pytest.mark.anyio
async def test_queues_count_students_not_questions(monkeypatch):
    """「多少人撞上」才是共性 —— 一个学生问十遍不如两个学生各问一遍。"""
    # 知识点读的是**项目**挂的那道赛题的课程分类（见模块说明：今天没有更细的一维），
    # 学生就是项目的主人 —— 所以「两个学生撞上同一处」是**两个项目**归到同一个分类，
    # 不是两个人的发言进了同一个房间。
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    carol = project("Carol 的作业", "carol", 3)
    loop_room = topic("循环作业")
    recursion_room = topic("递归作业")
    session = FakeSession(
        projects=[alice, bob, carol],
        tasks=[task(1, 11), task(2, 12), task(3, 12)],
        topics=[loop_room, recursion_room],
        categories=[category(11, "循环"), category(12, "递归")],
        users=[
            user("alice", "爱丽丝"),
            user("bob", "鲍勃"),
            user("carol", "卡罗"),
        ],
        blocks=[
            # 循环: 一个人问了十遍。
            *[
                block(alice, content=f"循环第 {i} 问", topic_id=loop_room.id)
                for i in range(10)
            ],
            # 递归: 两个人各问一遍。
            block(bob, content="递归看不懂", topic_id=recursion_room.id),
            block(carol, content="递归出口在哪", topic_id=recursion_room.id),
        ],
    )
    svc = _service(monkeypatch, session)

    data = await svc.queues(
        space_id=7, handle="teacher", student=None, from_ts=None, to_ts=None
    )

    top = data["stuckPoints"][0]
    assert top["knowledgePoint"] == "递归"
    assert top["studentCount"] == 2
    assert top["questionCount"] == 2

    loop = data["stuckPoints"][1]
    assert loop["knowledgePoint"] == "循环"
    assert loop["studentCount"] == 1
    assert loop["questionCount"] == 10


@pytest.mark.anyio
async def test_queues_every_row_can_point_back_at_a_real_message(monkeypatch):
    """队列里没有一行是点不回去的 —— 例子带的是原文坐标，不是拼出来的话。"""
    alice = project("Alice 的作业", "alice", 1)
    room = topic("循环作业")
    newest = block(alice, content="最新那句", created_at=LATER, topic_id=room.id)
    session = FakeSession(
        projects=[alice],
        tasks=[task(1, 11)],
        topics=[room],
        categories=[category(11, "循环")],
        users=[user("alice", "爱丽丝")],
        blocks=[block(alice, content="早一点那句", topic_id=room.id), newest],
    )
    svc = _service(monkeypatch, session)

    data = await svc.queues(
        space_id=7, handle="teacher", student=None, from_ts=None, to_ts=None
    )

    point = data["stuckPoints"][0]
    assert point["latestAt"] == int(LATER.timestamp() * 1000)
    assert point["example"]["blockId"] == str(newest.id)
    assert point["example"]["topicId"] == str(room.id)


@pytest.mark.anyio
async def test_queues_keep_questions_that_belong_to_no_knowledge_point(monkeypatch):
    """没归类的发言照样进队列 —— 它们不能被筛没，只是没有知识点可讲。"""
    alice = project("Alice 的作业", "alice", None)
    session = FakeSession(
        projects=[alice],
        users=[user("alice", "爱丽丝")],
        blocks=[block(alice)],
    )
    svc = _service(monkeypatch, session)

    data = await svc.queues(
        space_id=7, handle="teacher", student=None, from_ts=None, to_ts=None
    )

    assert len(data["stuckPoints"]) == 1
    assert data["stuckPoints"][0]["knowledgePoint"] is None
    assert data["stuckPoints"][0]["questionCount"] == 1


@pytest.mark.anyio
async def test_queues_are_empty_rather_than_broken_when_nothing_was_said(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    svc = _service(monkeypatch, FakeSession(projects=[alice]))

    data = await svc.queues(
        space_id=7, handle="teacher", student=None, from_ts=None, to_ts=None
    )

    assert data["stuckPoints"] == []


# ---------------------------------------------------------------------------
# 筛出来的发言
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_questions_are_scoped_to_the_readable_students(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    session = FakeSession(
        projects=[alice, bob],
        users=[user("alice", "爱丽丝"), user("bob", "鲍勃")],
        blocks=[block(alice), block(bob, content="Bob 的疑问")],
    )
    svc = _service(monkeypatch, session, readable=[alice.id])

    data = await svc.questions(
        space_id=7,
        handle="teacher",
        student=None,
        from_ts=None,
        to_ts=None,
        knowledge_point=None,
    )

    assert [q["student"] for q in data["questions"]] == ["alice"]
    assert data["total"] == 1


@pytest.mark.anyio
async def test_questions_filter_by_student(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    session = FakeSession(
        projects=[alice, bob],
        users=[user("alice", "爱丽丝"), user("bob", "鲍勃")],
        blocks=[block(alice), block(bob)],
    )
    svc = _service(monkeypatch, session)

    data = await svc.questions(
        space_id=7,
        handle="teacher",
        student="bob",
        from_ts=None,
        to_ts=None,
        knowledge_point=None,
    )

    assert [q["studentName"] for q in data["questions"]] == ["鲍勃"]


@pytest.mark.anyio
async def test_questions_filter_by_knowledge_point(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    session = FakeSession(
        projects=[alice, bob],
        tasks=[task(1, 11), task(2, 12)],
        categories=[category(11, "循环"), category(12, "递归")],
        users=[user("alice", "爱丽丝"), user("bob", "鲍勃")],
        blocks=[block(alice, content="循环那句"), block(bob, content="递归那句")],
    )
    svc = _service(monkeypatch, session)

    data = await svc.questions(
        space_id=7,
        handle="teacher",
        student=None,
        from_ts=None,
        to_ts=None,
        knowledge_point=12,
    )

    assert [q["quote"] for q in data["questions"]] == ["递归那句"]
    assert data["questions"][0]["knowledgePoint"] == "递归"


@pytest.mark.anyio
async def test_questions_respect_the_time_window(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    session = FakeSession(
        projects=[alice],
        users=[user("alice", "爱丽丝")],
        blocks=[
            block(alice, content="三月问的", created_at=NOW),
            block(
                alice,
                content="四月问的",
                created_at=datetime(2026, 4, 10, 9, 0, tzinfo=UTC),
            ),
        ],
    )
    svc = _service(monkeypatch, session)

    data = await svc.questions(
        space_id=7,
        handle="teacher",
        student=None,
        from_ts=int(NOW.timestamp() * 1000),
        to_ts=int(datetime(2026, 3, 31, tzinfo=UTC).timestamp() * 1000),
        knowledge_point=None,
    )

    assert [q["quote"] for q in data["questions"]] == ["三月问的"]


@pytest.mark.anyio
async def test_every_question_carries_the_coordinates_to_jump_back(monkeypatch):
    """「点回原文」是这一格的硬要求：每条都得带房间和消息 id。"""
    alice = project("Alice 的作业", "alice", 1)
    room = topic("循环作业")
    session = FakeSession(
        projects=[alice],
        topics=[room],
        users=[user("alice", "爱丽丝")],
        blocks=[block(alice, topic_id=room.id)],
    )
    svc = _service(monkeypatch, session)

    data = await svc.questions(
        space_id=7,
        handle="teacher",
        student=None,
        from_ts=None,
        to_ts=None,
        knowledge_point=None,
    )

    item = data["questions"][0]
    uuid.UUID(item["blockId"])
    uuid.UUID(item["topicId"])
    uuid.UUID(item["projectId"])
    assert item["topicTitle"] == "循环作业"
    assert item["projectName"] == "Alice 的作业"


# ---------------------------------------------------------------------------
# 提纲
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_outline_groups_the_picked_quotes_into_sessions(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    loop_room = topic("循环作业")
    recursion_room = topic("递归作业")
    loop_block = block(alice, content="循环里的 i 从哪来", topic_id=loop_room.id)
    recursion_block = block(bob, content="递归什么时候停", topic_id=recursion_room.id)
    session = FakeSession(
        projects=[alice, bob],
        tasks=[task(1, 11), task(2, 12)],
        topics=[loop_room, recursion_room],
        categories=[category(11, "循环"), category(12, "递归")],
        users=[user("alice", "爱丽丝"), user("bob", "鲍勃")],
        blocks=[loop_block, recursion_block],
        space_name="程序设计基础",
    )
    svc = _service(monkeypatch, session)

    data = await svc.outline(
        space_id=7,
        handle="teacher",
        block_ids=[loop_block.id, recursion_block.id],
    )

    assert data["title"] == "程序设计基础 · 共性问题讲解提纲"
    assert data["missing"] == []
    assert [s["session"] for s in data["sections"]] == [1, 2]
    assert {s["knowledgePoint"] for s in data["sections"]} == {"循环", "递归"}

    excerpt = data["sections"][0]["excerpts"][0]
    assert excerpt["blockId"] == str(loop_block.id)
    assert excerpt["quote"] == "循环里的 i 从哪来"
    uuid.UUID(excerpt["topicId"])


@pytest.mark.anyio
async def test_outline_drops_blocks_the_asker_may_no_longer_read(monkeypatch):
    """勾的时候能读，不等于递提纲的时候还能读 —— 撤了权的就进 missing。"""
    alice = project("Alice 的作业", "alice", 1)
    bob = project("Bob 的作业", "bob", 2)
    kept = block(alice, content="读得了的那句")
    revoked = block(bob, content="读不了的那句")
    session = FakeSession(
        projects=[alice, bob],
        users=[user("alice", "爱丽丝"), user("bob", "鲍勃")],
        blocks=[kept, revoked],
    )
    svc = _service(monkeypatch, session, readable=[alice.id])

    data = await svc.outline(
        space_id=7, handle="teacher", block_ids=[kept.id, revoked.id]
    )

    assert data["missing"] == [str(revoked.id)]
    quotes = [e["quote"] for s in data["sections"] for e in s["excerpts"]]
    assert quotes == ["读得了的那句"]


@pytest.mark.anyio
async def test_outline_reports_ids_that_are_not_messages_at_all(monkeypatch):
    alice = project("Alice 的作业", "alice", 1)
    kept = block(alice)
    session = FakeSession(
        projects=[alice], users=[user("alice", "爱丽丝")], blocks=[kept]
    )
    stranger = uuid.uuid4()
    svc = _service(monkeypatch, session)

    data = await svc.outline(
        space_id=7, handle="teacher", block_ids=[kept.id, stranger]
    )

    assert data["missing"] == [str(stranger)]
    assert len(data["sections"]) == 1


@pytest.mark.anyio
async def test_outline_of_nothing_is_empty_not_an_error(monkeypatch):
    svc = _service(monkeypatch, FakeSession(), project_ids=[])

    data = await svc.outline(space_id=7, handle="teacher", block_ids=[])

    assert data == {"title": "", "sections": [], "missing": []}


# ---------------------------------------------------------------------------
# 学生的发言是哪几条 —— 假 session 判不了，编译出来看
# ---------------------------------------------------------------------------


class TestStudentQuestionQuery:
    """一条学生发言的判据：参与者写的、不是芝士写的、而且不在私聊里。"""

    def _sql(self, **kwargs) -> str:
        kwargs.setdefault("from_dt", None)
        kwargs.setdefault("to_dt", None)
        stmt = SpaceLearningService(session=None)._student_question_blocks(  # type: ignore[arg-type]
            [uuid.uuid4()], **kwargs
        )
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    def test_excludes_agent_handles_by_the_reserved_prefix(self):
        sql = self._sql()
        assert "NOT (blocks.author = 'cheese'" in sql
        assert "blocks.author LIKE 'cheese-'" in sql

    def test_excludes_private_rooms(self):
        assert "topics.is_private IS false" in self._sql()

    def test_reads_only_participant_messages(self):
        sql = self._sql()
        assert "blocks.author_type = 'participant'" in sql
        assert "blocks.kind = 'message'" in sql

    def test_bounds_the_window_when_one_is_given(self):
        sql = self._sql(from_dt=NOW, to_dt=LATER)
        assert "blocks.created_at >= '2026-03-10 09:00:00+00:00'" in sql
        assert "blocks.created_at <= '2026-03-11 09:00:00+00:00'" in sql

    def test_leaves_the_window_open_when_none_is_given(self):
        assert "blocks.created_at >=" not in self._sql()

    def test_is_capped_so_the_board_cannot_be_used_as_a_full_transcript(self):
        assert f"LIMIT {QUESTION_LIMIT}" in self._sql()


# ---------------------------------------------------------------------------
# 摘要与时间戳
# ---------------------------------------------------------------------------


class TestExcerpt:
    def test_collapses_newlines_into_one_line(self):
        assert _excerpt("第一行\n\n第二行") == "第一行 第二行"

    def test_keeps_short_quotes_whole(self):
        assert _excerpt("短的一句") == "短的一句"

    def test_marks_the_cut_so_the_reader_knows_there_is_more(self):
        result = _excerpt("长" * 500)
        assert result.endswith("…")
        assert len(result) == 161


class TestMilliseconds:
    def test_none_stays_none(self):
        assert _from_ms(None) is None
        assert _to_ms(None) is None

    def test_round_trips_through_utc(self):
        stamp = int(NOW.timestamp() * 1000)
        assert _from_ms(stamp) == NOW

    def test_naive_input_is_read_as_utc(self):
        assert _from_ms(0) == datetime(1970, 1, 1, tzinfo=UTC)
