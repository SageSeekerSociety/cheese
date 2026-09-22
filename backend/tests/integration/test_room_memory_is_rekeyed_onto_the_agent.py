"""房间攒下的记忆，重键之后同一位芝士还是召回同一批事实。

记忆以前按**房间**记（scope_id 是 `<项目>:cheese-<房间 id 前 12 位 hex>`），读侧再
拿这个键补一次。今天 agent 只有一个从它自己派生的名字，那条按房间的读路径连同铸造
它的函数一起删掉了——所以扫不到的行就是再也读不到的行，而记忆是显式写进去、不可
再生的（结论 61）。这条用例守的就是那一步没有丢东西。

跑的是迁移里那段真实 SQL（从迁移模块 import），不是照抄一份——照抄测的就不是要发
布的东西。
"""

import importlib.util
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.memory.models import MemoryScope, agent_project_scope_id
from app.domain.memory.store import memory_store
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d5c48f1a6b73_an_agent_signs_with_its_instance_handle.py"
)


def _rekey_sql() -> str:
    spec = importlib.util.spec_from_file_location("_rekey_agent_memory", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REKEY_AGENT_MEMORY


def _room_pool(project_id: uuid.UUID, room_id: uuid.UUID) -> str:
    """旧代码按房间记账时用的那个 scope_id。

    铸它的函数已经删了，所以这里逐字写出库里存着的字符串：这条用例面对的就是存量。
    """
    return agent_project_scope_id(project_id, f"cheese-{room_id.hex[:12]}")


def test_the_same_cheese_recalls_the_same_facts_after_the_rekey(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """重键前房间池里的事实，重键后在芝士自己的池里，一条不少。

    目标池本来就有行（同一位芝士在新键下写的），两边并成一个池——一个项目里的芝士
    只有一份记忆，这正是要的结果。连跑两遍一个字不差：窗口里旧镜像还在往房间键上写
    新行，这段 SQL 要由下一条迁移原样再跑一遍。
    """

    async def run() -> None:
        project = await ProjectService(db_session).create(
            name="Rekey", forge_kind="github_app"
        )
        room = await TopicService(db_session).create(
            project_id=project.id, title="学到东西的那间房", created_by="alice"
        )
        agent = await AgentInstanceService(db_session).for_project(project)
        scope, own = memory_pool(project.id, agent)
        store = memory_store(db_session)
        await store.remember(scope, own, "它早就知道的那件事")
        await store.remember(
            MemoryScope.agent_project,
            _room_pool(project.id, room.id),
            "这条是按房间分池时代记下的",
        )
        # 另一个项目的同名形状不许被顺手带走。
        other = await ProjectService(db_session).create(
            name="Bystander", forge_kind="github_app"
        )
        await store.remember(
            MemoryScope.agent_project,
            _room_pool(other.id, room.id),
            "这条不属于那个项目",
        )
        await db_session.flush()

        room_pool = _room_pool(project.id, room.id)
        before = set(await store.recall(scope, own)) | set(
            await store.recall(MemoryScope.agent_project, room_pool)
        )

        for _ in range(2):
            await db_session.execute(sa.text(_rekey_sql()))
            await db_session.flush()

            assert set(await store.recall(scope, own)) == before
            assert await store.recall(MemoryScope.agent_project, room_pool) == []
            # 隔壁项目那条既没被搬走，也没被搬进这个池。
            assert await store.recall(
                MemoryScope.agent_project, _room_pool(other.id, room.id)
            ) == ["这条不属于那个项目"]

    _portal.call(run)
