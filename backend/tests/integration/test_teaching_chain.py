"""AI 队友辅导在真库上的读法：空间和题目两层同时生效，项目集不读，项目整份替换。

`tests/unit/test_task_teaching.py` 盯的是 `resolve` 的**规则**；这里盯的是它在
**真库**上、走 `for_project` 那条读路时各层各就各位。

用 `seed_task_with_protocol` 建题：它能一次把空间设置、项目集上的旧配置、题目自己
的设置都摆好，读回来的是 `app.domain.task.teaching.for_project` —— 就是芝士开新
会话时走的那一段，不是另写一条只供测试用的捷径。
"""

import asyncio

from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task import teaching as teaching_context
from tests.conftest import seed_task_with_protocol, start_task_project

OWNER = "owner-1"

#: 每一层写一个不同的要求，谁生效、是不是两层都在，一眼能看出来。
SPACE = {"mode": "guide", "system_prompt": "空间的要求", "material_ids": []}
CATEGORY = {"mode": "free", "system_prompt": "项目集的"}
TASK = {"system_prompt": "题目的"}
PROJECT = {"mode": "example", "system_prompt": "项目的"}


def _project(client, *, external_task_id: int) -> str:
    return start_task_project(client, external_task_id, handle=OWNER)


def _set_project_teaching(client, *, project_id: int, teaching: dict) -> None:
    """A project's own `settings['teaching']` — the INNERMOST level, the one a
    项目 sets for itself after it was created."""

    async def _run() -> None:
        async with client.test_factory() as session:
            project = await session.get(Project, project_id)
            project.settings = {**(project.settings or {}), "teaching": teaching}
            await session.commit()

    asyncio.run(_run())


def _chain(client, project_id: int):
    """`for_project` for one project — the real read path, end to end."""

    async def _run():
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(project_id)
            return await teaching_context.for_project(session=session, project=project)

    return asyncio.run(_run())


def test_the_space_reaches_a_project_with_nothing_on_its_task(client):
    """只有空间说了话：它生效。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.mode == "guide"
    assert context.teaching.system_prompt == "空间的要求"
    assert context.teaching.space_prompt is None


def test_a_category_is_not_read(client):
    """项目集上还留着的旧配置不生效：它的设置已经挪到它的题目上。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE, teaching=CATEGORY)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.mode == "guide"
    assert context.teaching.system_prompt == "空间的要求"


def test_the_space_and_the_task_are_both_in_force(client):
    """题目加了自己的要求：两份都到 agent 手上，没选方式就沿用空间的。"""
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE,
        teaching=CATEGORY,
        override={"teaching": TASK},
    )
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.mode == "guide"
    assert context.teaching.space_prompt == "空间的要求"
    assert context.teaching.system_prompt == "题目的"


def test_an_empty_task_setting_leaves_the_space_alone(client):
    """题目那一格在，但是空的 —— 空 = 没说话，空间那份照旧。"""
    task_id = seed_task_with_protocol(
        client, space_teaching=SPACE, override={"teaching": {}}
    )
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.system_prompt == "空间的要求"
    assert context.teaching.space_prompt is None


def test_a_projects_own_setting_replaces_both_levels(client):
    """项目自己的 `settings` 配了：两层都让位，用它这一份。"""
    task_id = seed_task_with_protocol(
        client, space_teaching=SPACE, override={"teaching": TASK}
    )
    project_id = _project(client, external_task_id=task_id)
    _set_project_teaching(client, project_id=project_id, teaching=PROJECT)
    context = _chain(client, project_id)

    assert context is not None
    assert context.teaching.mode == "example"
    assert context.teaching.system_prompt == "项目的"
    assert context.teaching.space_prompt is None


def test_a_chain_that_says_nothing_at_any_level_is_still_empty(client):
    """哪一层都没配 —— 没有上下文，一次多余的查询都不发。"""
    task_id = seed_task_with_protocol(client)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is None
