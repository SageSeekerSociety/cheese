"""AI 队友辅导的取数那一半 (#8d772257)。

`tests/unit/test_task_teaching.py` 盯的是「解析成什么、拼进 prompt 长什么样」；这
里盯的是**为谁去查库**：从课程建的项目拿到辅导设置与课件，而一个不是课程的项目
在这里一次查询都不发。「一个字都不多加载」的另一半在这里，因为「没有查询」只能
对着真库数出来。

查询数是 SQLAlchemy 的 `before_cursor_execute` 数的 —— 数的是真发出去的语句，
不是我们以为会发出的语句。
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import event

from app.domain.living_doc.services import Documents
from app.domain.materials.models import Material
from app.domain.project.repositories import ProjectRepository
from app.domain.task import teaching as teaching_context
from app.domain.task.models import Task
from tests.conftest import seed_task_with_protocol, start_task_project
from tests.integration.conftest import a_team, post_project

OWNER = "owner-1"
GUIDE = {"mode": "guide", "system_prompt": "只讲循环，不碰递归。"}


def _project(client, *, task_id: int | None = None) -> str:
    """A project 开始做 ``task_id`` makes, or a plain one when there is no task."""
    if task_id is not None:
        return start_task_project(client, task_id, handle=OWNER)
    return post_project(client, json={"name": "团队"}, owner=OWNER).json()["data"]["id"]


def _seed_reference_rows(client, *, material: str, document: str) -> tuple[int, str]:
    """A 课件 and a team document — the two things a teaching config points at."""
    ids: dict[str, int | str] = {}

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
            doc = await Documents(session).create(
                project_id=None,
                team_id=await a_team(session),
                title=document,
                author="owner",
            )
            doc.content = "循环讲了什么"
            await session.flush()
            ids["document"] = str(doc.id)
            await session.commit()

    asyncio.run(_seed())
    return int(ids["material"]), str(ids["document"])


def _set_teaching(client, *, task_id: int, teaching: dict) -> None:
    """Rewrite the 题目's own guidance, as its edit endpoint would."""

    async def _run() -> None:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            task.protocol_override = {
                **(task.protocol_override or {}),
                "teaching": teaching,
            }
            await session.commit()

    asyncio.run(_run())


def _for_project(client, project_id: str):
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


def test_a_course_project_starts_with_its_guidance(client):
    material_id, document_id = _seed_reference_rows(
        client, material="L08 讲义", document="循环与数组"
    )
    task_id = seed_task_with_protocol(
        client,
        override={
            "teaching": {
                **GUIDE,
                "material_ids": [material_id],
                "document_ids": [document_id],
            }
        },
    )
    project_id = _project(client, task_id=task_id)

    context, _ = _for_project(client, project_id)

    assert context is not None
    assert context.course == "创研课 2026 秋"  # 项目集的名字就是课程名
    assert context.teaching.mode == "guide"
    assert context.teaching.system_prompt == "只讲循环，不碰递归。"
    assert [m["name"] for m in context.materials] == ["L08 讲义"]
    assert context.materials[0]["url"] == "https://example.invalid/l08.pdf"
    assert [d["title"] for d in context.documents] == ["循环与数组"]
    # 标题进 prompt，正文按 id 读
    assert "content" not in context.documents[0]


def test_a_project_that_is_not_a_course_asks_the_database_nothing(client):
    """不是从题目开始的项目不是课程建的：一次查询都不发。

    这条是「非课程项目一个字都不多加载」的另一半 —— 另半（不渲染那一段）在
    `tests/unit/test_task_teaching.py`。少了这一条，一个多查一次库的实现照样
    能全绿。
    """
    project_id = _project(client)

    context, sent = _for_project(client, project_id)

    assert context is None
    assert sent == []


def test_a_course_that_configured_nothing_resolves_to_no_context(client):
    task_id = seed_task_with_protocol(client)  # 有项目集，没有教学配置
    project_id = _project(client, task_id=task_id)

    context, sent = _for_project(client, project_id)

    assert context is None
    # 一条 join 找到题目与项目集就够；课件与知识各一次都不查
    assert len(sent) == 1


def test_the_guidance_is_read_fresh_not_copied_into_the_project(client):
    """老师改了辅导方式：同一个项目，下一轮读到的是改过的。

    这是生效语义里「已有项目的新会话」那一半 —— 配置不是注册时抄进项目的副本。
    """
    task_id = seed_task_with_protocol(client, override={"teaching": GUIDE})
    project_id = _project(client, task_id=task_id)
    assert _for_project(client, project_id)[0].teaching.mode == "guide"

    _set_teaching(client, task_id=task_id, teaching={**GUIDE, "mode": "spec"})

    context, _ = _for_project(client, project_id)
    assert context.teaching.mode == "spec"
    assert context.teaching.system_prompt == GUIDE["system_prompt"]
