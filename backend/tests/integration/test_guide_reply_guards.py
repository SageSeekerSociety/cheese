"""引导为主的两项保护，落在 AI 队友发出的一条消息上（``POST /topics/{id}/messages``）。

规则（先写下来、再写的代码）：

- 题目选了「引导为主」并打开「输出检查」：AI 回复里的一大段代码，房间里读到的是一句
  提示，并记下一次；
- 打开「提示阶梯」：回复开头的级数标记被读走、记下，房间里看不到它；
- 不是引导模式的题、不是课程的项目：同样的回复原样落进房间，一个字都不改，什么都不记。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.block.models import Block
from app.domain.project.models import Project
from app.domain.space.models import Space, SpaceCategory
from app.domain.task.guide import OUTPUT_CHECK_LINES
from app.domain.task.models import Task, TaskGuideEvent
from tests.integration.conftest import post_project, room_agent_headers

CODE = "\n".join(f"total_{n} = {n} * 2" for n in range(OUTPUT_CHECK_LINES + 2))
REPLY = f"[[hint:4]]\n关键是这几行：\n```python\n{CODE}\n```"


def _course_project(client, teaching: dict | None) -> tuple[str, str]:
    """alice 的一个项目；``teaching`` 不为空时它出自一道带这份配置的题。"""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    if teaching is not None:

        async def link() -> None:
            async with client.test_factory() as db:
                now = datetime.now(UTC)
                space = Space(
                    name=f"课-{uuid.uuid4().hex[:8]}",
                    intro="",
                    description="",
                    task_templates=[],
                    teaching=teaching,
                    created_at=now,
                    updated_at=now,
                )
                db.add(space)
                await db.flush()
                category = SpaceCategory(
                    space_id=space.id,
                    name="分组",
                    description="",
                    display_order=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(category)
                await db.flush()
                task = Task(
                    name="第 3 题",
                    intro="",
                    description="",
                    creator_id=0,
                    space_id=space.id,
                    category_id=category.id,
                    submitter_type=0,
                    approved=1,
                    default_deadline=0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(task)
                await db.flush()
                row = await db.get(Project, uuid.UUID(project["id"]))
                assert row is not None
                row.external_task_id = task.id
                await db.commit()

        asyncio.run(link())
    return project["id"], project["root_topic_id"]


def _say(client, room: str, content: str) -> dict:
    resp = client.post(
        f"/topics/{room}/messages",
        json={"request_id": str(uuid.uuid4()), "content": content},
        headers=room_agent_headers(client, room),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _stored(client, block_id: str) -> tuple[str, list[tuple[str, int | None]]]:
    async def read():
        async with client.test_factory() as db:
            block = await db.get(Block, uuid.UUID(block_id))
            events = (
                await db.execute(
                    select(TaskGuideEvent.kind, TaskGuideEvent.level)
                    .where(TaskGuideEvent.block_id == uuid.UUID(block_id))
                    .order_by(TaskGuideEvent.id)
                )
            ).all()
            assert block is not None
            return block.content, [(k, lv) for k, lv in events]

    return asyncio.run(read())


def test_a_guide_task_turns_a_long_block_into_a_hint_and_records_both_guards(client):
    _project, room = _course_project(
        client, {"mode": "guide", "hint_ladder": True, "output_check": True}
    )
    sent = _say(client, room, REPLY)

    content, events = _stored(client, sent["id"])
    assert "total_5" not in content
    assert "[[hint:" not in content
    assert content.startswith("关键是这几行")
    assert events == [("hint", 4), ("output_check", OUTPUT_CHECK_LINES + 2)]


def test_a_task_in_another_mode_leaves_the_reply_as_it_is(client):
    _project, room = _course_project(
        client, {"mode": "example", "hint_ladder": True, "output_check": True}
    )
    sent = _say(client, room, REPLY)

    content, events = _stored(client, sent["id"])
    assert content == REPLY
    assert events == []


def test_a_project_outside_any_course_leaves_the_reply_as_it_is(client):
    _project, room = _course_project(client, None)
    sent = _say(client, room, REPLY)

    content, events = _stored(client, sent["id"])
    assert content == REPLY
    assert events == []
