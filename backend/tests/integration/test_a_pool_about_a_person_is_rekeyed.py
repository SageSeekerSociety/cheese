"""关于一个人的那些记忆，重键之后还在，而且落在了正确的那位芝士名下。

``user`` 池从前是跨项目的一个池（scope_id 就是那个人的 handle）。重键之后读侧只认
``<项目>:<agent>:<人>``，所以扫不到的行就是再也读不到的行——而记忆是显式写进去、
不可再生的（结论 61）。这条用例守的就是那一步没有丢东西，也没有把 A 项目的观察送
进 B 项目。

跑的是迁移里那段真实 SQL（从迁移模块 import），不是照抄一份。连跑两遍：dev 先跑迁
移后换容器，窗口里旧镜像还在按老键写新行，这段要由 P36 的迁移原样再跑一遍。
"""

import importlib.util
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.memory.models import MemoryScope, user_scope_id
from app.domain.memory.store import memory_store
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c9a4e2f71d38_a_pool_about_a_person_belongs_to_one_agent.py"
)


def _rekey_statements() -> list[str]:
    """迁移真正会跑的那几句，按它自己的顺序。

    从 `rekey_personal_memory` 里收出来，而不是照着 SQL 常量抄一遍：抄的那一份哪
    天和发布的这一份分了岔，用例还是绿的。
    """
    spec = importlib.util.spec_from_file_location("_rekey_personal_memory", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    collected: list[str] = []
    module.rekey_personal_memory(collected.append)
    return collected


async def _rekey(session: AsyncSession) -> None:
    for sql in _rekey_statements():
        await session.execute(sa.text(sql))
    await session.flush()


def test_what_was_learned_about_a_person_lands_on_the_agent_that_learned_it(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    async def run() -> None:
        store = memory_store(db_session)

        mine = await ProjectService(db_session).create(
            name="有私聊的那个项目", owner_handle="andyl", forge_kind="github_app"
        )
        await TopicService(db_session).get_or_create_private(
            project_id=mine.id, user_handle="andyl"
        )
        # 同一个人在另一个项目里也是成员，但没有私聊——归属的依据是私聊，所以这个
        # 项目不该分到那条记忆。
        bystander = await ProjectService(db_session).create(
            name="只是成员的那个项目", owner_handle="andyl", forge_kind="github_app"
        )
        mine_agent = await AgentInstanceService(db_session).for_project(mine)
        bystander_agent = await AgentInstanceService(db_session).for_project(bystander)

        # 存量：跨项目那个池里的一行，键就是他的 handle。
        await store.remember(MemoryScope.user, "andyl", "他要结论在最前面")
        await db_session.flush()

        landed = user_scope_id(mine.id, mine_agent.handle, "andyl")
        not_landed = user_scope_id(bystander.id, bystander_agent.handle, "andyl")

        for _ in range(2):
            await _rekey(db_session)

            assert await store.recall(MemoryScope.user, landed) == ["他要结论在最前面"]
            assert await store.count(MemoryScope.user, not_landed) == 0
            # 老键上一行不剩：新代码不认它，留着就是一条谁也读不到的记忆。
            assert await store.count(MemoryScope.user, "andyl") == 0

    _portal.call(run)


def test_a_person_with_no_private_chat_lands_under_each_projects_cheese(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """一间私聊都没有的人无从归属，按计划落到他所在项目的默认芝士名下。

    少给一份就是让那个项目的芝士从此不知道这件事，而没有第二个地方能把它找回来。
    """

    async def run() -> None:
        store = memory_store(db_session)
        project = await ProjectService(db_session).create(
            name="没有私聊", owner_handle="bob", forge_kind="github_app"
        )
        agent = await AgentInstanceService(db_session).for_project(project)
        await store.remember(MemoryScope.user, "bob", "他习惯当天回消息")
        await db_session.flush()

        await _rekey(db_session)

        assert await store.recall(
            MemoryScope.user, user_scope_id(project.id, agent.handle, "bob")
        ) == ["他习惯当天回消息"]

    _portal.call(run)


def test_a_person_in_nothing_at_all_keeps_his_rows(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """归不到任何地方的行留着，不删。读不到还能由后一条迁移补搬；删了就没了。"""

    async def run() -> None:
        store = memory_store(db_session)
        stranger = f"nobody-{uuid.uuid4().hex[:8]}"
        await store.remember(MemoryScope.user, stranger, "谁也不认识他")
        await db_session.flush()

        await _rekey(db_session)

        assert await store.recall(MemoryScope.user, stranger) == ["谁也不认识他"]

    _portal.call(run)
