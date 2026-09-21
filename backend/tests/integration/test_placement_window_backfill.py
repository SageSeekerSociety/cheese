"""部署窗口里旧镜像写下的位置，要被认领到它自己那条会话上。

dev 是先跑迁移后换容器，所以 #1308 的迁移跑完到新镜像起来之间，还有一个旧镜像在往
`topics.session_placement` 写位置。丢了它就是那间房下一轮找不到自己的机器：手上那
棵工作树、装好的环境、正开着的屏，全部要重来一遍。

跑的是迁移里那段真实 SQL（从迁移模块 import），不是照抄一份——照抄测的就不是要
发布的东西。`topics.session_placement` 这一列还在（它要等 device connection owner
发过一轮才掉），所以测试直接往上落一条旧镜像会写的位置。
"""

import importlib.util
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "e6b4c92a07d1_claim_window_placements.py"
)


def _claim_sql() -> str:
    spec = importlib.util.spec_from_file_location("_claim_placements", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CLAIM_ROOM_PLACEMENTS


def test_the_window_placement_lands_on_the_session_that_owns_it(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """一个房间一条旧位置，房间里却坐着两条会话：它归骨架对得上的那一条。

    摊给两条会话，那条 pi 的屏会被中心通道当成自己的去 restore。已经有位置的会话
    一律不碰——`settled` 的骨架和它房间那条旧位置是对得上的，挡住它的只剩「已经
    有位置了」这一条，所以这段 SQL 连跑两遍，三行的结果一个字都不差。
    """

    async def _run() -> None:
        project = Project(name="Window", owner_handle="alice")
        db_session.add(project)
        await db_session.flush()

        def _room(title: str) -> Topic:
            room = Topic(project_id=project.id, title=title, kind=TopicKind.topic)
            db_session.add(room)
            return room

        two_agents = _room("两条会话")
        already = _room("已经有位置了")
        await db_session.flush()

        pi = AgentSession(topic_id=two_agents.id, agent_handle="pi-agent", harness="pi")
        claude = AgentSession(
            topic_id=two_agents.id, agent_handle="cheese", harness="claude-code"
        )
        settled_location = {
            "device_id": "its-own",
            "channel": "device",
            "resource_id": str(uuid.uuid4()),
        }
        settled_lease = {"kind": "device", "device_id": "its-own-hands"}
        # 骨架和它房间那条旧位置一样是 pi：把它挡在认领之外的，只剩 SQL 里
        # `AND s.runtime_location IS NULL` 这一条。骨架不一样的话，这条用例
        # 红的会是骨架匹配，幂等这半边一个字也没测到。
        settled = AgentSession(
            topic_id=already.id,
            agent_handle="cheese",
            harness="pi",
            runtime_location=settled_location,
            work_lease=settled_lease,
        )
        db_session.add_all([pi, claude, settled])
        await db_session.flush()

        resource = str(uuid.uuid4())
        placement = {
            "device_id": "center",
            "channel": "device",
            "resource_id": resource,
            "runtime": {"harness": "pi"},
            "execution": {"kind": "device", "device_id": "executor"},
        }
        for room_id, value in ((two_agents.id, placement), (already.id, placement)):
            await db_session.execute(
                text(
                    "UPDATE topics SET session_placement = CAST(:value AS json)"
                    " WHERE id = :id"
                ),
                {"value": json.dumps(value), "id": room_id},
            )

        async def _claim_once() -> dict[uuid.UUID, tuple]:
            await db_session.execute(text(_claim_sql()))
            db_session.expunge_all()
            return {
                row.id: (row.work_lease, row.runtime_location, row.placed_at)
                for row in (
                    await db_session.execute(
                        select(
                            AgentSession.id,
                            AgentSession.work_lease,
                            AgentSession.runtime_location,
                            AgentSession.placed_at,
                        )
                    )
                ).all()
            }

        first = await _claim_once()

        # 骨架对得上的那一条拿到了手和位置，而位置里不带手。
        assert first[pi.id][0] == {"kind": "device", "device_id": "executor"}
        assert first[pi.id][1] == {
            "device_id": "center",
            "channel": "device",
            "resource_id": resource,
            "runtime": {"harness": "pi"},
        }
        assert first[pi.id][2] is not None

        # 同一个房间里的另一条会话没有被摊上一个假位置。
        assert first[claude.id][:2] == (None, None)

        # 已经有位置的不碰：它手上的还是它自己那一份。
        assert first[settled.id][:2] == (settled_lease, settled_location)

        # 重跑一遍，三行一个字都不差——这才是「跑几遍结果都一样」被测到。
        assert await _claim_once() == first

    _portal.call(_run)
