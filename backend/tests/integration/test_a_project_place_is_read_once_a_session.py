"""发一条消息问三次「他在这个项目里坐哪」，同一个会话只查一次库。

房间准入、角色门槛和路由各问一次 `standing`，参数相同、会话相同，每次答案五条
语句。这里数的是真发出去的语句（`before_cursor_execute`），不是我们以为会发的。

缓存只有在它跟着写入和事务边界走的时候才对：一次 flush 可能改变谁在哪里（加人、
移除、排除、改角色），Core 语句写下的行不经过 flush，事务或 savepoint 回滚也会
把问过的那个改动收回去，而提交之后新开的读本来能看到别的请求在中间提交了什么。
所以除了「第二次不问库」，这里还测三次翻转 —— 先问为负、加人后为正；先问为正、
移除或排除后为负；提交后别的会话删掉成员，再问也必须拿到新答案 —— 外加 savepoint
回滚不留下正结果。去掉 `core/request_facts.py` 里 `before_flush` 的清理，移除成员
那条必须变红。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import event

from app.auth.project_roles import standing
from app.domain.membership.repositories import MemberRepository
from app.domain.project_permissions import Seat
from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    post_project,
)


class _Undo(Exception):
    """Raised inside a savepoint to roll only that savepoint back."""


def _project(client, owner: str = "alice") -> uuid.UUID:
    response = post_project(client, json={"name": "P"}, owner=owner)
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["id"])


def _counter(session):
    """Count every statement ``session`` sends until the returned call is made."""
    sent: list[str] = []

    def _count(_conn, _cursor, statement, _parameters, _context, _many):
        sent.append(statement)

    engine = session.get_bind()
    engine = getattr(engine, "sync_engine", engine)
    event.listen(engine, "before_cursor_execute", _count)

    def _stop() -> None:
        event.remove(engine, "before_cursor_execute", _count)

    return sent, _stop


def test_a_place_asked_for_three_times_is_read_once(client):
    project_id = _project(client)
    add_external_member(client, str(project_id), "bob", by="alice")

    async def _run():
        async with client.test_factory() as session:
            sent, stop = _counter(session)
            try:
                first = await standing(session, project_id, "bob")
                after_first = len(sent)
                second = await standing(session, project_id, "bob")
                third = await standing(session, project_id, "bob")
            finally:
                stop()
            await session.rollback()
        return first, second, third, after_first, len(sent)

    first, second, third, after_first, after_third = asyncio.run(_run())
    assert first is not None and first.seat is Seat.member
    # The first ask really read the rows — otherwise "no more statements"
    # would be true of a session that never answered anything.
    assert after_first >= 1
    assert second == first and third == first
    assert after_third == after_first


def test_asking_before_adding_someone_gives_the_new_answer_after(client):
    project_id = _project(client)

    async def _run():
        async with client.test_factory() as session:
            assert await standing(session, project_id, "bob") is None
            await MemberRepository(session).add(
                project_id=project_id, user_handle="bob"
            )
            found = await standing(session, project_id, "bob")
            await session.rollback()
            return found

    found = asyncio.run(_run())
    assert found is not None and found.seat is Seat.member


def test_asking_before_removing_someone_gives_the_new_answer_after(client):
    project_id = _project(client)
    add_external_member(client, str(project_id), "bob", by="alice")

    async def _run():
        async with client.test_factory() as session:
            assert await standing(session, project_id, "bob") is not None
            member = await MemberRepository(session).get(
                project_id=project_id, user_handle="bob"
            )
            assert member is not None
            await MemberRepository(session).delete(member)
            found = await standing(session, project_id, "bob")
            await session.rollback()
            return found

    assert asyncio.run(_run()) is None


def test_asking_before_excluding_a_team_member_gives_the_new_answer_after(client):
    project_id = _project(client)
    join_project_team(client, str(project_id), "bob")

    async def _run():
        async with client.test_factory() as session:
            assert await standing(session, project_id, "bob") is not None
            await MemberRepository(session).exclude(
                project_id=project_id, user_handle="bob"
            )
            found = await standing(session, project_id, "bob")
            await session.rollback()
            return found

    assert asyncio.run(_run()) is None


def test_committing_drops_an_answer_another_request_changed_meanwhile(client):
    project_id = _project(client)
    add_external_member(client, str(project_id), "bob", by="alice")

    async def _run():
        async with client.test_factory() as first, client.test_factory() as second:
            assert await standing(first, project_id, "bob") is not None
            member = await MemberRepository(second).get(
                project_id=project_id, user_handle="bob"
            )
            assert member is not None
            await MemberRepository(second).delete(member)
            await second.commit()
            # The request ends its transaction and goes on reading; the answer
            # it read before the commit belongs to the transaction that ended.
            await first.commit()
            found = await standing(first, project_id, "bob")
            await first.rollback()
            return found

    assert asyncio.run(_run()) is None


def test_a_savepoint_that_rolled_back_leaves_no_answer_behind(client):
    project_id = _project(client)

    async def _run():
        async with client.test_factory() as session:
            assert await standing(session, project_id, "bob") is None
            with pytest.raises(_Undo):
                async with session.begin_nested():
                    await MemberRepository(session).add(
                        project_id=project_id, user_handle="bob"
                    )
                    assert await standing(session, project_id, "bob") is not None
                    raise _Undo
            found = await standing(session, project_id, "bob")
            await session.rollback()
            return found

    assert asyncio.run(_run()) is None
