"""私聊的两席在名册上：「谁被点名」由此推出，不需要 @（结论 19、20）。

一间私聊就是项目内名册两席的房间。所以「对面是谁」只有名册一个出处——谁答这间房、
个人记忆记在谁名下、未读按谁归类、再打开是不是同一间，四个问题问的都是这两席。

每个用例都先把 ``topics.private_owner`` / ``private_peer`` 两列清空再断言。那两列记
的正是同一件事，记在了第二个地方；清空它们，剩下的行为就只能是名册答出来的。P13 会
把两列删掉，到那时这些用例一个字都不用改。
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import session_auth_headers

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f1a9c3e07b42_a_private_chats_two_seats_live_in_the_roster.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("_two_seats", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _forget_the_columns(client, topic_id: str) -> None:
    """把两列清空——P13 会把它们删掉，这里先让它们说不出话。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE topics SET private_owner=NULL, private_peer=NULL"
                    " WHERE id=:t"
                ),
                {"t": uuid.UUID(topic_id)},
            )
            await session.commit()

    asyncio.run(_run())


def _seats(client, topic_id: str) -> tuple[str, str] | None:
    async def _run() -> tuple[str, str] | None:
        async with client.test_factory() as session:
            return await TopicMemberService(session).private_seats(uuid.UUID(topic_id))

    return asyncio.run(_run())


def _who_answers(client, topic_id: str) -> str:
    """这一轮没人被 @ 的时候，答这间房的是谁——它的 handle。"""

    async def _run() -> str:
        async with client.test_factory() as session:
            topic = await TopicRepository(session).get(uuid.UUID(topic_id))
            assert topic is not None
            return (await TopicService(session).resolve_agent(topic)).handle

    return asyncio.run(_run())


def _badge(client, project_id: str, user: str) -> dict:
    """这个人的私聊角标：对面是谁 → 几条未读。"""
    r = client.get(
        f"/projects/{project_id}/private-unread",
        params={"handle": user},
        headers=session_auth_headers(user),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _seat_by_hand(client, topic_id: str, handle: str) -> None:
    """名册上直接加一行——存量三席的私聊就是这么来的：``e7d2b91a4c06`` 补上了队友
    那一席，``d5c48f1a6b73`` 因为房里坐着别的实例把替身留了下来。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO topic_memberships"
                    " (id, topic_id, member_handle, role, created_at, updated_at)"
                    " VALUES (gen_random_uuid(), :t, :h, 'member', now(), now())"
                ),
                {"t": uuid.UUID(topic_id), "h": handle},
            )
            await session.commit()

    asyncio.run(_run())


def _say(client, project_id: str, topic_id: str, author: str) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                topic_id=uuid.UUID(topic_id),
                author=author,
                author_type=AuthorType.participant,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    asyncio.run(_run())


def test_a_dm_names_its_teammate_from_the_roster(client):
    """合同：私聊的名册恰好两席，「谁被点名」由此推出。"""
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    seats = _seats(client, dm)
    assert seats is not None
    reviewer_seat = seats[1]

    _forget_the_columns(client, dm)

    assert _seats(client, dm) == ("user-1", reviewer_seat)
    assert _who_answers(client, dm) == "reviewer"


def test_personal_memory_in_a_dm_is_authorized_by_the_two_seats(client):
    """个人记忆只在当事人自己的私聊里读写——当事人是谁，名册说了算。"""
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1")
    _forget_the_columns(client, dm)

    mine = client.post(
        f"/projects/{project_id}/memory",
        json={
            "content": "偏好简洁汇报",
            "scope": "user",
            "owner": "user-1",
            "topic": dm,
        },
    )
    assert mine.status_code == 200, mine.text

    someone_elses = client.post(
        f"/projects/{project_id}/memory",
        json={
            "content": "别人的事",
            "scope": "user",
            "owner": "user-2",
            "topic": dm,
        },
    )
    assert someone_elses.status_code == 403, someone_elses.text


def test_a_dm_badge_is_keyed_by_the_other_seat(client):
    """未读按对面那一席归类——人按 handle，队友按 agent: 前缀。"""
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    with_person = _dm(client, project_id, "user-1", peer_handle="mentor-1")
    with_reviewer = _dm(client, project_id, "user-1", agent_handle="reviewer")
    reviewer_seats = _seats(client, with_reviewer)
    assert reviewer_seats is not None
    _forget_the_columns(client, with_person)
    _forget_the_columns(client, with_reviewer)

    _say(client, project_id, with_person, "mentor-1")
    _say(client, project_id, with_reviewer, reviewer_seats[1])

    assert _badge(client, project_id, "user-1") == {"mentor-1": 1, "agent:reviewer": 1}


def test_opening_the_same_dm_again_finds_it_by_its_two_seats(client):
    """再打开是同一间房，认的是两席，不是那两列。"""
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    with_reviewer = _dm(client, project_id, "user-1", agent_handle="reviewer")
    with_person = _dm(client, project_id, "user-1", peer_handle="mentor-1")
    _forget_the_columns(client, with_reviewer)
    _forget_the_columns(client, with_person)

    assert _dm(client, project_id, "user-1", agent_handle="reviewer") == with_reviewer
    # 人对人的那间两边都找得到，谁先开的不影响。
    assert _dm(client, project_id, "mentor-1", peer_handle="user-1") == with_person
    assert with_reviewer != with_person


def test_an_old_dms_two_seats_are_backfilled(client):
    """存量私聊：名册空着、两列还在，迁移把两席补回来，这间房又答得出对面是谁。

    跑的是迁移自己的 ``only_the_two_parties``，不是照抄一份 SQL。
    """
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    seats = _seats(client, dm)
    assert seats is not None
    reviewer_seat = seats[1]

    async def _age_and_backfill() -> None:
        async with client.test_factory() as session:
            # 名册还没有这间房的时候建的私聊：两列有值，席位一行没有。
            await session.execute(
                sa.text("DELETE FROM topic_memberships WHERE topic_id=:t"),
                {"t": uuid.UUID(dm)},
            )
            await session.commit()
        async with client.test_factory() as session:
            members = TopicMemberService(session)
            assert await members.private_seats(uuid.UUID(dm)) is None
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _migration().only_the_two_parties(sync.exec_driver_sql)
            )
            await session.commit()

    asyncio.run(_age_and_backfill())

    assert _seats(client, dm) == ("user-1", reviewer_seat)
    assert _who_answers(client, dm) == "reviewer"

    async def _again() -> int:
        async with client.test_factory() as session:
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _migration().only_the_two_parties(sync.exec_driver_sql)
            )
            await session.commit()
        async with client.test_factory() as session:
            return int(
                await session.scalar(
                    sa.text("SELECT count(*) FROM topic_memberships WHERE topic_id=:t"),
                    {"t": uuid.UUID(dm)},
                )
            )

    # 幂等：再跑一遍不多一行。
    assert asyncio.run(_again()) == 2


def test_reopening_a_dm_whose_roster_grew_opens_a_two_seat_one(client):
    """名册多出一席的房间不再是这间 DM：再打开给的是一间正好两席、答得出对面的房。

    这样的房间在迁移跑完之前就有，部署窗口里上一版接口也还加得出来。认它就是让
    「再打开这间 DM」落进一间说不出对面是谁的房间——同时匹配上好几间时，返回哪一
    间更是没有定数。
    """
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    grown = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _seat_by_hand(client, grown, f"cheese-{uuid.UUID(grown).hex[:12]}")
    assert _seats(client, grown) is None

    again = _dm(client, project_id, "user-1", agent_handle="reviewer")

    assert again != grown
    assert _seats(client, again) == ("user-1", reviewer["seat_handle"])
    assert _who_answers(client, again) == "reviewer"
    # 两席的那间从此是唯一认得出的一间，再打开还是它。
    assert _dm(client, project_id, "user-1", agent_handle="reviewer") == again


def test_an_old_dms_extra_seats_are_unseated(client):
    """存量三席的私聊：迁移跑完回到两席，这间房又答得出对面是谁。

    迁移之前它不是私聊，角标里就不该有它——既不翻倍，也不多出一行归给别人。
    """
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _say(client, project_id, dm, reviewer["seat_handle"])
    assert _badge(client, project_id, "user-1") == {"agent:reviewer": 1}

    # e7d2b91a4c06 补上队友那一席，d5c48f1a6b73 因为房里坐着别的实例留下了替身。
    _seat_by_hand(client, dm, f"cheese-{uuid.UUID(dm).hex[:12]}")

    assert _seats(client, dm) is None
    assert _badge(client, project_id, "user-1") == {}

    async def _run_the_migration() -> None:
        async with client.test_factory() as session:
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _migration().only_the_two_parties(sync.exec_driver_sql)
            )
            await session.commit()

    asyncio.run(_run_the_migration())

    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
    assert _badge(client, project_id, "user-1") == {"agent:reviewer": 1}
    assert _who_answers(client, dm) == "reviewer"

    # 幂等：再跑一遍不动任何一行。
    asyncio.run(_run_the_migration())
    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
