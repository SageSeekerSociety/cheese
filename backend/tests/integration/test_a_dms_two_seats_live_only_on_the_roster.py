"""存量私聊在 ``topics`` 那两列被删掉之前，先被收成名册上的两席。

上一次发布到这一次之间有一段窗口，那段时间里旧镜像建的私聊只写了
``private_owner`` / ``private_peer`` 两列，名册上一行也没有。列一删，它们就再也说
不出对面是谁：AI 私聊退回项目默认那位芝士（换了默认就换了人），个人记忆的读写被
拒，未读角标整间房消失。

所以 ``b3f7a1c9d204`` 在同一条迁移里先回填、再删列，而用例跑的就是它那个
``the_columns_go``，不是照抄一份 SQL。次序反过来这条就红。

用例自己把那两列装回去，因为库已经在 head 上、列已经没了——装的是上一条迁移留下
的形状（可空 varchar(64)），也只有这么装才有「窗口里那批私聊」可测。
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b3f7a1c9d204_a_dms_two_seats_live_only_on_the_roster.py"
)

ADD_THEM_BACK = (
    "ALTER TABLE topics ADD COLUMN IF NOT EXISTS private_owner varchar(64)",
    "ALTER TABLE topics ADD COLUMN IF NOT EXISTS private_peer varchar(64)",
)
TAKE_THEM_AWAY = (
    "ALTER TABLE topics DROP COLUMN IF EXISTS private_owner",
    "ALTER TABLE topics DROP COLUMN IF EXISTS private_peer",
)


def _migration():
    spec = importlib.util.spec_from_file_location("_columns_go", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(client, statements) -> None:
    async def _go() -> None:
        async with client.test_factory() as session:
            for statement in statements:
                await session.execute(sa.text(statement))
            await session.commit()

    asyncio.run(_go())


@pytest.fixture
def as_of_the_two_columns(client):
    """把库退回上一条迁移的形状：那两列在，值由用例自己写。

    隔离靠的是每条用例前的 TRUNCATE，不是事务回滚，所以 DDL 会留在库里——
    收尾这一步不是礼貌，是下一条用例能不能跑的前提。
    """
    _run(client, ADD_THEM_BACK)
    try:
        yield
    finally:
        _run(client, TAKE_THEM_AWAY)


def _project(client) -> str:
    return client.post("/projects", json={"name": "Demo"}).json()["data"]["id"]


def _add_agent(client, project_id: str, handle: str, name: str) -> dict:
    r = client.post(
        f"/projects/{project_id}/agents",
        json={"handle": handle, "display_name": name},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _dm(client, project_id: str, user: str, **params) -> str:
    r = client.get(
        f"/projects/{project_id}/private-chat",
        params={"user_handle": user, **params},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _as_the_old_image_left_it(client, topic_id: str, owner: str, peer: str) -> None:
    """窗口里旧镜像建的私聊：两列有值，席位一行没有。"""
    _run(
        client,
        (
            f"UPDATE topics SET private_owner='{owner}', private_peer='{peer}'"
            f" WHERE id='{topic_id}'",
            f"DELETE FROM topic_memberships WHERE topic_id='{topic_id}'",
        ),
    )


def _seats(client, topic_id: str) -> tuple[str, str] | None:
    async def _go() -> tuple[str, str] | None:
        async with client.test_factory() as session:
            return await TopicMemberService(session).private_seats(uuid.UUID(topic_id))

    return asyncio.run(_go())


def _who_answers(client, topic_id: str) -> str:
    async def _go() -> str:
        async with client.test_factory() as session:
            topic = await TopicRepository(session).get(uuid.UUID(topic_id))
            assert topic is not None
            return (await TopicService(session).resolve_agent(topic)).handle

    return asyncio.run(_go())


def _columns(client) -> set[str]:
    async def _go() -> set[str]:
        async with client.test_factory() as session:
            rows = await session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name='topics'"
                )
            )
            return {row[0] for row in rows}

    return asyncio.run(_go())


def _the_columns_go(client) -> None:
    """跑迁移自己的那两步，次序也是它自己的。"""

    async def _go() -> None:
        async with client.test_factory() as session:
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _migration().the_columns_go(sync.exec_driver_sql)
            )
            await session.commit()

    asyncio.run(_go())


def test_a_window_era_dm_is_seated_before_the_columns_go(client, as_of_the_two_columns):
    """合同：窗口里建的私聊在列消失之前被收回两席，之后仍答得出对面是谁。"""
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _as_the_old_image_left_it(client, dm, "user-1", reviewer["seat_handle"])
    assert _seats(client, dm) is None

    _the_columns_go(client)

    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
    assert _who_answers(client, dm) == "reviewer"
    assert "private_owner" not in _columns(client)
    assert "private_peer" not in _columns(client)
