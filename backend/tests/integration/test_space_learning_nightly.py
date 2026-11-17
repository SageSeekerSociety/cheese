"""每晚的学情分析：读谁的什么、写下什么、什么时候不推进、什么时候收手。

真库（``business_db_factory``）、真的分析任务（``run_nightly``），只有网关是假的：
一个 MockTransport 签 key、报价、按测试排好的话作答（或者答 500）。每条用例断的
都是分析之后库里能读回来的东西 —— 判断、依据、游标、归类、调用次数 —— 以及模型
读到了什么（它收到的那份材料）。

规则（先写下来、再写的代码）：

- 正常一晚：成员在题目房间里的发言被读，判断带着依据写下，提问被归类，游标停在读过
  的最后一条；同一天再醒一次不再调用；
- 模型调用失败：什么都不写，游标不动，下一次醒来重读同一批；
- 每块板每天的调用上限：到了就停，没轮到的成员留到明天（游标不动）；
- 退出这块板的成员不再被读；
- 指不出依据（编号对不上这一批里成员自己的发言）的判断被丢掉；
- 私聊里的话不读；被标「不准」的判断在下一次分析时交给模型复核，复核后清掉标记。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.models import Project
from app.domain.space import learning_insight_analysis
from app.domain.space.models import (
    Space,
    SpaceCategory,
    SpaceKnowledgePoint,
    SpaceLearningCursor,
    SpaceLearningJudgment,
    SpaceLearningQuestion,
    SpaceLearningRun,
    SpaceMember,
)
from app.domain.task.models import Task
from app.domain.topic.models import new_private_chat
from app.domain.usage import pricing
from tests.integration.conftest import project_service, registered

#: 「这一晚」：Asia/Shanghai 03:00，过了默认的 2 点开跑。
NIGHT = datetime(2026, 11, 17, 3, 0, tzinfo=learning_insight_analysis.LOCAL_ZONE)
SAID_AT = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
POINT = "相关子查询"


class Gateway:
    """签 key、报价、按排好的顺序作答的假网关。``answers`` 里一项是一段回答，或者
    一个 HTTP 状态码（那一次就答这个码）。``asked`` 是模型收到的每一份材料。"""

    def __init__(self) -> None:
        self.answers: list[str | int] = []
        self.asked: list[str] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            return httpx.Response(200, json={"key": "sk-learning-analysis"})
        if request.url.path == "/model/info":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "model_name": "deepseek-flash",
                            "litellm_params": {
                                "model": "deepseek-flash",
                                "input_cost_per_token": 0.0000005,
                                "output_cost_per_token": 0.0000015,
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.asked.append(body["messages"][-1]["content"])
            answer = self.answers.pop(0)
            if isinstance(answer, int):
                return httpx.Response(answer, json={"error": "down"})
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": answer}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 300, "completion_tokens": 60},
                },
            )
        return httpx.Response(404)


@pytest.fixture
def gateway(monkeypatch) -> Gateway:
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    monkeypatch.setattr(settings, "learning_analysis_model", "deepseek-flash")
    monkeypatch.setattr(settings, "learning_analysis_run_hour", 2)
    monkeypatch.setattr(settings, "learning_analysis_daily_calls_per_space", 50)
    monkeypatch.setattr(settings, "learning_analysis_batch_messages", 40)
    pricing.forget()
    return Gateway()


def _answer(judgments: list[dict], questions: list[dict] | None = None) -> str:
    return json.dumps({"judgments": judgments, "questions": questions or []})


async def _board(session, members: list[str]) -> dict:
    """一块开着学情分析的板、一道题、一个知识点，每个成员一个从这道题开的项目。"""
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:8]
    owner_id = await registered(session, f"teacher-{tag}")
    space = Space(
        name=f"学情-{tag}",
        intro="",
        description="",
        task_templates=[],
        learning_analysis_enabled=True,
        learning_analysis_enabled_by=owner_id,
        learning_analysis_enabled_at=SAID_AT - timedelta(days=1),
        created_at=now,
        updated_at=now,
    )
    session.add(space)
    await session.flush()
    category = SpaceCategory(
        space_id=space.id,
        name="分组",
        description="",
        display_order=0,
        created_at=now,
        updated_at=now,
    )
    session.add(category)
    await session.flush()
    task = Task(
        name="第 3 题",
        intro="",
        description="",
        creator_id=owner_id,
        space_id=space.id,
        category_id=category.id,
        submitter_type=0,
        approved=1,
        default_deadline=0,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    point = SpaceKnowledgePoint(
        space_id=space.id,
        name=POINT,
        display_order=0,
        created_at=now,
        updated_at=now,
    )
    session.add(point)
    await session.flush()
    people: dict[str, dict] = {}
    for name in members:
        handle = f"{name}-{tag}"
        user_id = await registered(session, handle)
        session.add(
            SpaceMember(
                space_id=space.id, user_id=user_id, created_at=now, updated_at=now
            )
        )
        project = await project_service(session).create(name="P", owner_handle=handle)
        row = await session.get(Project, project.id)
        assert row is not None
        row.external_task_id = task.id
        people[name] = {
            "handle": handle,
            "user_id": user_id,
            "project_id": project.id,
            "room": project.root_topic_id,
        }
    await session.flush()
    return {
        "space_id": space.id,
        "task_id": task.id,
        "point_id": point.id,
        "people": people,
    }


def _say(session, person: dict, content: str, at: datetime, *, author=None, room=None):
    block = Block(
        id=uuid.uuid4(),
        project_id=person["project_id"],
        conversation_id=room or person["room"],
        author=author or person["handle"],
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content=content,
        created_at=at,
        updated_at=at,
    )
    session.add(block)
    return block.id


async def _rows(factory, model, **where):
    async with factory() as session:
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return list((await session.scalars(stmt)).all())


async def _night(factory, gateway: Gateway, now: datetime = NIGHT):
    return await learning_insight_analysis.run_nightly(
        factory, now=now, transport=gateway.transport
    )


@pytest.mark.anyio
async def test_a_night_reads_a_members_words_and_writes_judgments_with_evidence(
    business_db_factory, gateway
):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        first = _say(session, ann, "子查询里怎么引用外层那一行？", SAID_AT)
        _say(
            session,
            ann,
            "用 t.dept 引用外层。",
            SAID_AT + timedelta(minutes=1),
            author=CHEESE_HANDLE,
        )
        second = _say(session, ann, "我改成这样对吗？", SAID_AT + timedelta(minutes=2))
        await session.commit()

    gateway.answers.append(
        _answer(
            [
                {
                    "point": POINT,
                    "level": "weak",
                    "summary": "你还不清楚内层怎样引用外层那一行。",
                    "evidence": ["m1", "m2"],
                }
            ],
            [
                {
                    "ref": "m1",
                    "point": POINT,
                    "kind": "concept",
                    "gist": "怎么引用外层",
                },
                {"ref": "m2", "point": POINT, "kind": "verify", "gist": "验证写法"},
            ],
        )
    )
    counts = await _night(factory, gateway)
    assert counts["calls"] == 1

    # 模型读到了成员的两条话，以及跟在第一条后面的那段芝士回复（作为上下文）。
    (prompt,) = gateway.asked
    assert "子查询里怎么引用外层那一行？" in prompt
    assert "我改成这样对吗？" in prompt
    assert "用 t.dept 引用外层。" in prompt

    (judgment,) = await _rows(factory, SpaceLearningJudgment, user_id=ann["user_id"])
    assert judgment.level == "weak"
    assert judgment.knowledge_point_id == world["point_id"]
    assert set(judgment.evidence_block_ids) == {first, second}
    assert judgment.disputed_at is None

    filed = await _rows(factory, SpaceLearningQuestion, user_id=ann["user_id"])
    assert {(q.block_id, q.kind) for q in filed} == {
        (first, "concept"),
        (second, "verify"),
    }

    (cursor,) = await _rows(factory, SpaceLearningCursor, user_id=ann["user_id"])
    assert cursor.last_block_id == second
    assert cursor.messages_read == 2

    # 同一天再醒一次：这一晚已经跑完，不再调用。
    again = await _night(factory, gateway, NIGHT + timedelta(hours=1))
    assert again["calls"] == 0
    assert len(gateway.asked) == 1


@pytest.mark.anyio
async def test_nothing_runs_before_the_night_starts(business_db_factory, gateway):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        _say(session, world["people"]["ann"], "问一个问题", SAID_AT)
        await session.commit()

    counts = await _night(factory, gateway, NIGHT.replace(hour=1))
    assert counts["calls"] == 0
    assert gateway.asked == []


@pytest.mark.anyio
async def test_a_failed_call_writes_nothing_and_leaves_the_cursor_where_it_was(
    business_db_factory, gateway
):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        said = _say(session, ann, "WHERE 里为什么不能写 AVG？", SAID_AT)
        await session.commit()

    gateway.answers.append(500)
    counts = await _night(factory, gateway)
    assert counts["failed"] == 1
    assert await _rows(factory, SpaceLearningJudgment, user_id=ann["user_id"]) == []
    assert await _rows(factory, SpaceLearningCursor, user_id=ann["user_id"]) == []
    (run,) = await _rows(factory, SpaceLearningRun, space_id=world["space_id"])
    assert run.calls == 1  # the failed call still counts against the cap
    assert run.finished_at is None

    # 下一次醒来：同一批重读，这次写下。
    gateway.answers.append(
        _answer(
            [{"point": POINT, "level": "shaky", "summary": "x", "evidence": ["m1"]}]
        )
    )
    await _night(factory, gateway, NIGHT + timedelta(hours=1))
    assert "WHERE 里为什么不能写 AVG？" in gateway.asked[-1]
    (cursor,) = await _rows(factory, SpaceLearningCursor, user_id=ann["user_id"])
    assert cursor.last_block_id == said


@pytest.mark.anyio
async def test_the_daily_cap_stops_the_night_and_leaves_the_rest_for_tomorrow(
    business_db_factory, gateway, monkeypatch
):
    monkeypatch.setattr(settings, "learning_analysis_daily_calls_per_space", 1)
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann", "bob"])
        _say(session, world["people"]["ann"], "ann 的问题", SAID_AT)
        _say(session, world["people"]["bob"], "bob 的问题", SAID_AT)
        await session.commit()

    gateway.answers.append(_answer([]))
    counts = await _night(factory, gateway)
    assert counts["calls"] == 1
    assert counts["capped"] == 1
    (run,) = await _rows(factory, SpaceLearningRun, space_id=world["space_id"])
    assert run.capped and run.finished_at is not None
    # 只有一个人被读了；另一个人的游标没动，明晚接着读。
    cursors = await _rows(factory, SpaceLearningCursor, space_id=world["space_id"])
    assert len(cursors) == 1

    gateway.answers.append(_answer([]))
    tomorrow = await _night(factory, gateway, NIGHT + timedelta(days=1))
    assert tomorrow["calls"] == 1
    cursors = await _rows(factory, SpaceLearningCursor, space_id=world["space_id"])
    assert len(cursors) == 2


@pytest.mark.anyio
async def test_a_member_who_left_the_space_is_no_longer_read(
    business_db_factory, gateway
):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        _say(session, ann, "退出之前说的话", SAID_AT)
        membership = (
            await session.scalars(
                select(SpaceMember).where(
                    SpaceMember.space_id == world["space_id"],
                    SpaceMember.user_id == ann["user_id"],
                )
            )
        ).one()
        membership.deleted_at = datetime.now(UTC)
        await session.commit()

    counts = await _night(factory, gateway)
    assert counts["calls"] == 0
    assert gateway.asked == []
    assert await _rows(factory, SpaceLearningCursor, user_id=ann["user_id"]) == []


@pytest.mark.anyio
async def test_a_judgment_that_points_at_nothing_it_read_is_dropped(
    business_db_factory, gateway
):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        session.add(
            SpaceKnowledgePoint(
                space_id=world["space_id"],
                name="GROUP BY 与 HAVING",
                display_order=1,
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        _say(session, ann, "HAVING 和 WHERE 有什么区别", SAID_AT)
        await session.commit()

    gateway.answers.append(
        _answer(
            [
                # 编号 m9 不在这一批里：指不出依据，整条丢掉。
                {"point": POINT, "level": "weak", "summary": "x", "evidence": ["m9"]},
                # 没写依据：同样丢掉。
                {"point": "GROUP BY 与 HAVING", "level": "weak", "summary": "y"},
            ]
        )
    )
    await _night(factory, gateway)
    assert await _rows(factory, SpaceLearningJudgment, user_id=ann["user_id"]) == []
    # 答案读得出来，只是没有站得住的判断：这一批算读过了。
    (cursor,) = await _rows(factory, SpaceLearningCursor, user_id=ann["user_id"])
    assert cursor.messages_read == 1


@pytest.mark.anyio
async def test_a_private_chat_is_not_read(business_db_factory, gateway):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        private = new_private_chat(
            project_id=ann["project_id"], title="私聊", created_by=ann["handle"]
        )
        session.add(private)
        await session.flush()
        _say(session, ann, "频道里的问题", SAID_AT)
        _say(session, ann, "私聊里的心里话", SAID_AT, room=private.id)
        await session.commit()

    gateway.answers.append(_answer([]))
    await _night(factory, gateway)
    (prompt,) = gateway.asked
    assert "频道里的问题" in prompt
    assert "私聊里的心里话" not in prompt


@pytest.mark.anyio
async def test_a_disputed_judgment_is_rechecked_and_the_mark_is_cleared(
    business_db_factory, gateway
):
    factory = business_db_factory
    async with factory() as session:
        world = await _board(session, ["ann"])
        ann = world["people"]["ann"]
        old = _say(session, ann, "旧的问题", SAID_AT)
        await session.flush()
        session.add(
            SpaceLearningJudgment(
                space_id=world["space_id"],
                user_id=ann["user_id"],
                knowledge_point_id=world["point_id"],
                level="weak",
                summary="以前的判断",
                evidence_block_ids=[old],
                disputed_at=SAID_AT,
                analyzed_at=SAID_AT,
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        session.add(
            SpaceLearningCursor(
                space_id=world["space_id"],
                user_id=ann["user_id"],
                task_id=world["task_id"],
                last_block_at=SAID_AT,
                last_block_id=old,
                messages_read=1,
                created_at=SAID_AT,
                updated_at=SAID_AT,
            )
        )
        new = _say(session, ann, "我自己写出来了", SAID_AT + timedelta(hours=1))
        await session.commit()

    gateway.answers.append(
        _answer(
            [{"point": POINT, "level": "mastered", "summary": "z", "evidence": ["m1"]}]
        )
    )
    await _night(factory, gateway)
    (prompt,) = gateway.asked
    # 只读游标之后的那条；旧判断带着「成员认为不准」交给模型复核。
    assert "我自己写出来了" in prompt and "旧的问题" not in prompt
    assert "以前的判断" in prompt and "不准" in prompt

    (judgment,) = await _rows(factory, SpaceLearningJudgment, user_id=ann["user_id"])
    assert judgment.level == "mastered"
    assert judgment.disputed_at is None
    assert judgment.evidence_block_ids[0] == new
    assert old in judgment.evidence_block_ids
