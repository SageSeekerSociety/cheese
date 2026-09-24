"""课程级教学配置的取数那一半 (#8d772257)。

`tests/unit/test_task_teaching.py` 盯的是「解析成什么、拼进 prompt 长什么样」；这
里盯的是**为谁去查库**：从课程建的项目拿到本周范围与课件，而一个不是课程的项目
在这里一次查询都不发。「一个字都不多加载」的另一半在这里，因为「没有查询」只能
对着真库数出来。

查询数是 SQLAlchemy 的 `before_cursor_execute` 数的 —— 数的是真发出去的语句，
不是我们以为会发出的语句。
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import event

from app.domain.knowledge.models import Knowledge
from app.domain.materials.models import Material
from app.domain.project.repositories import ProjectRepository
from app.domain.space.models import SpaceCategory
from app.domain.task import teaching as teaching_context
from app.domain.task.models import Task
from tests.conftest import seed_task_with_protocol
from tests.integration.conftest import post_project

OWNER = "owner-1"
WEEK_THREE = {
    "system_prompt": "第 {current_week} 周，只做 {allowed_topics}。",
    "current_week": 3,
    "allowed_topics": ["循环"],
    "avoid_in_code": ["递归"],
}


def _project(client, *, external_task_id: int | None = None, name: str = "团队") -> int:
    body: dict = {"name": name, "owner_handle": OWNER}
    if external_task_id is not None:
        body["external_task_id"] = external_task_id
    return post_project(client, json=body).json()["data"]["id"]


def _seed_reference_rows(client, *, material: str, knowledge: str) -> tuple[int, int]:
    """A 课件 and a 知识 material — the two things a teaching config points at."""
    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:
            now = datetime.now(UTC)
            row = Material(
                type="file",
                url="https://example.invalid/l08.pdf",
                name=material,
                uploader_id=1,
                created_at=now,
                meta={},
            )
            session.add(row)
            await session.flush()
            ids["material"] = row.id
            entry = Knowledge(
                name=knowledge,
                description="讲次 L08",
                type="TEXT",
                content={"text": "循环讲了什么"},
                team_id=1,
                created_by=1,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            await session.flush()
            ids["knowledge"] = entry.id
            await session.commit()

    asyncio.run(_seed())
    return ids["material"], ids["knowledge"]


def _set_teaching(client, *, task_id: int, teaching: dict) -> None:
    """Rewrite the 项目集's teaching config, as its edit endpoint would."""

    async def _run() -> None:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            category = await session.get(SpaceCategory, task.category_id)
            category.teaching = teaching
            await session.commit()

    asyncio.run(_run())


def _for_project(client, project_id: int):
    """`for_project` for one project, plus every statement it sent to do it."""
    got: dict = {}

    async def _run() -> None:
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(project_id)
            sent: list[str] = []

            def _count(conn, cursor, statement, parameters, context, executemany):
                sent.append(statement)

            engine = session.get_bind()
            engine = getattr(engine, "sync_engine", engine)
            event.listen(engine, "before_cursor_execute", _count)
            try:
                got["context"] = await teaching_context.for_project(
                    session=session, project=project
                )
            finally:
                event.remove(engine, "before_cursor_execute", _count)
            got["sent"] = sent

    asyncio.run(_run())
    return got["context"], got["sent"]


def test_a_course_project_starts_inside_this_weeks_scope(client):
    material_id, knowledge_id = _seed_reference_rows(
        client, material="L08 讲义", knowledge="循环与数组"
    )
    task_id = seed_task_with_protocol(
        client,
        teaching={
            **WEEK_THREE,
            "material_ids": [material_id],
            "knowledge_ids": [knowledge_id],
        },
    )
    project_id = _project(client, external_task_id=task_id)

    context, _ = _for_project(client, project_id)

    assert context is not None
    assert context.course == "创研课 2026 秋"  # 项目集的名字就是课程名
    assert context.teaching.current_week == 3
    assert context.teaching.avoid_in_code == ["递归"]
    assert [m["name"] for m in context.materials] == ["L08 讲义"]
    assert context.materials[0]["url"] == "https://example.invalid/l08.pdf"
    assert [k["name"] for k in context.knowledge] == ["循环与数组"]
    # 名字与描述进 prompt，正文留在知识库里按 id 取
    assert "content" not in context.knowledge[0]


def test_a_project_that_is_not_a_course_asks_the_database_nothing(client):
    """没有 `external_task_id` 的项目不是课程建的：一次查询都不发。

    这条是「非课程项目一个字都不多加载」的另一半 —— 另半（不渲染那一段）在
    `tests/unit/test_task_teaching.py`。少了这一条，一个多查一次库的实现照样
    能全绿。
    """
    project_id = _project(client, external_task_id=None)

    context, sent = _for_project(client, project_id)

    assert context is None
    assert sent == []


def test_a_course_that_configured_nothing_resolves_to_no_context(client):
    task_id = seed_task_with_protocol(client)  # 有项目集，没有教学配置
    project_id = _project(client, external_task_id=task_id)

    context, sent = _for_project(client, project_id)

    assert context is None
    # 一条 join 找到题目与项目集就够；课件与知识各一次都不查
    assert len(sent) == 1


def test_the_week_is_read_fresh_not_copied_into_the_project(client):
    """项目集从第 3 周走到第 4 周：同一个项目，下一轮读到的是第 4 周。

    这是生效语义里「已有项目的新会话」那一半 —— 配置不是注册时抄进项目的副本，
    否则第二周就是错的。
    """
    task_id = seed_task_with_protocol(client, teaching=WEEK_THREE)
    project_id = _project(client, external_task_id=task_id)
    assert _for_project(client, project_id)[0].teaching.current_week == 3

    _set_teaching(client, task_id=task_id, teaching={**WEEK_THREE, "current_week": 4})

    context, _ = _for_project(client, project_id)
    assert context.teaching.current_week == 4
    assert context.teaching.system_prompt == WEEK_THREE["system_prompt"]
