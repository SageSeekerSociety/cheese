"""部署窗口里旧镜像写下的位置，`DROP COLUMN` 之前要被接住。

dev 是先跑迁移后换容器，所以 P18 的迁移跑完到新镜像起来之间，还有一个旧镜像在往
`topics.session_placement` 写位置。丢了它就是那间房下一轮找不到自己的机器：手上那
棵工作树、装好的环境、正开着的屏，全部要重来一遍。

跑的是迁移里那段真实 SQL（从迁移模块 import），不是照抄一份——照抄测的就不是要
发布的东西。列本身已经被这条迁移删掉了，所以测试先把它加回来，落一条旧镜像会写的
位置，再让 SQL 认领。
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
    / "e6b4c92a07d1_topics_drops_session_placement.py"
)


def _claim_sql() -> str:
    spec = importlib.util.spec_from_file_location("_drops_placement", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CLAIM_ROOM_PLACEMENTS


def test_the_window_placement_lands_on_the_session_that_owns_it(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """一个房间一条旧位置，房间里却坐着两条会话：它归骨架对得上的那一条。

    摊给两条会话，那条 pi 的屏会被中心通道当成自己的去 restore。已经有位置的会话
    一律不碰，所以这段 SQL 跑第二遍和跑第一遍结果一样。
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
        settled = AgentSession(
            topic_id=already.id,
            agent_handle="cheese",
            harness="claude-code",
            runtime_location=settled_location,
            work_lease={"kind": "device", "device_id": "its-own-hands"},
        )
        db_session.add_all([pi, claude, settled])
        await db_session.flush()

        # 旧镜像写的那一列。它已经不在 schema 里了，加回来才落得下去。
        await db_session.execute(
            text("ALTER TABLE topics ADD COLUMN session_placement JSON")
        )
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

        await db_session.execute(text(_claim_sql()))
        db_session.expunge_all()

        rows = {
            row.id: row
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

        # 骨架对得上的那一条拿到了手和位置，而位置里不带手。
        assert rows[pi.id].work_lease == {"kind": "device", "device_id": "executor"}
        assert rows[pi.id].runtime_location == {
            "device_id": "center",
            "channel": "device",
            "resource_id": resource,
            "runtime": {"harness": "pi"},
        }
        assert rows[pi.id].placed_at is not None

        # 同一个房间里的另一条会话没有被摊上一个假位置。
        assert rows[claude.id].runtime_location is None
        assert rows[claude.id].work_lease is None

        # 已经有位置的不碰——所以这段 SQL 重跑一遍结果不变。
        assert rows[settled.id].runtime_location == settled_location

    _portal.call(_run)
