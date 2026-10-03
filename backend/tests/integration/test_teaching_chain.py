"""`teaching` 的四级继承链 (#944)：空间 → 项目集 → 题目 → 项目。

`tests/unit/test_task_protocol.py` 盯的是 `resolve` 的**规则**（哪一层赢、怎么读
一层）；这里盯的是它在**真库**上、走 `for_project` 那条读路时，四级各就各位 —— 每
一层能盖过下面所有层、留空的那一层原样让位，而且盖过去的是**整份**而不是合在一
起。三级（项目集→题目→项目）在 #8d772257 时已经有 `test_teaching_context.py`
盖着，这一份补的是新加的最外层，以及它对整条链的影响。

用 `seed_task_with_protocol` 建题：它能一次把空间默认、项目集配置、题目覆盖都摆
好，读回来的是 `app.domain.task.teaching.for_project` —— 就是芝士开新会话时走的
那一段，不是另写一条只供测试用的捷径。
"""

import asyncio

from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.task import teaching as teaching_context
from tests.conftest import seed_claim, seed_task_with_protocol
from tests.integration.conftest import post_project

OWNER = "owner-1"

#: 每一层写一个不同的周次与 system prompt，谁赢了、赢的是整份都能一眼看出来。
SPACE = {
    "system_prompt": "空间的默认",
    "current_week": 1,
    "allowed_topics": ["空间话题"],
}
CATEGORY = {"system_prompt": "项目集的", "current_week": 2}
TASK = {"system_prompt": "题目的", "current_week": 3}
PROJECT = {"system_prompt": "项目的", "current_week": 4}


def _project(client, *, external_task_id: int) -> int:
    seed_claim(client, external_task_id, handle=OWNER)
    body = {"name": "团队", "external_task_id": external_task_id}
    return post_project(client, json=body, owner=OWNER).json()["data"]["id"]


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


def test_the_space_default_reaches_a_project_with_nothing_below_it(client):
    """四级里只有最外一层说了话：它生效。这是「空间设一份默认」的字面意思。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.current_week == 1
    assert context.teaching.system_prompt == "空间的默认"
    assert context.teaching.allowed_topics == ["空间话题"]


def test_a_blank_category_leaves_the_space_default_in_force(client):
    """题目留空、项目集也留空 —— 下层没说话，空间的默认照旧。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE, teaching={})
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.current_week == 1


def test_a_category_replaces_the_space_default_whole(client):
    """项目集配了：它赢。而且赢的是**整份** —— 空间那份里独有的 `allowed_topics`
    不跟着过来（不是合并）。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE, teaching=CATEGORY)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.current_week == 2
    assert context.teaching.system_prompt == "项目集的"
    assert context.teaching.allowed_topics == []


def test_a_task_override_replaces_the_category(client):
    """题目上的覆盖再往里一层：它赢，项目集那份整个让位。"""
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE,
        teaching=CATEGORY,
        override={"teaching": TASK},
    )
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.current_week == 3
    assert context.teaching.system_prompt == "题目的"


def test_an_empty_task_override_falls_back_to_the_category(client):
    """题目那一格在，但是空的 —— 空 = 没说话，所以项目集那份仍然生效。没有这条
    「空即继承」的规则，`{}`（每一行改造前的列默认）就会把上层的指导抹掉。"""
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE,
        teaching=CATEGORY,
        override={"teaching": {}},
    )
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is not None
    assert context.teaching.current_week == 2
    assert context.teaching.system_prompt == "项目集的"


def test_a_projects_own_setting_beats_every_level_above_it(client):
    """最里一层（项目自己的 `settings`）配了：四级全在，还是它赢。"""
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE,
        teaching=CATEGORY,
        override={"teaching": TASK},
    )
    project_id = _project(client, external_task_id=task_id)
    _set_project_teaching(client, project_id=project_id, teaching=PROJECT)
    context = _chain(client, project_id)

    assert context is not None
    assert context.teaching.current_week == 4
    assert context.teaching.system_prompt == "项目的"


def test_a_chain_that_says_nothing_at_any_level_is_still_empty(client):
    """四级都没配 —— `is_empty` 的语义没变：没有上下文，一次多余的查询都不发。"""
    task_id = seed_task_with_protocol(client)
    context = _chain(client, _project(client, external_task_id=task_id))

    assert context is None
