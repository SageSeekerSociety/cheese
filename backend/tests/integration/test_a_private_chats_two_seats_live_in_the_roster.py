"""私聊的两席在名册上：「谁被点名」由此推出，不需要 @（结论 19、20）。

一间私聊就是项目内名册两席的房间。所以「对面是谁」只有名册一个出处：谁答这间房、
个人记忆记在谁名下、未读按谁归类、再打开是不是同一间，四个问题问的都是这两席。

名册是唯一的出处：同一件事没有第二个地方记着，所以这里的每一条断言都只能是名册答
出来的。

后半部分是存量私聊怎么收成两席：``f1a9c3e07b42`` 的 ``only_the_two_parties``。它还
没有退役——``topics`` 那两列还在库上，``DROP COLUMN`` 那一条迁移会在删列之前把它原样
再跑一遍，接住这次发布窗口里旧镜像建的私聊。所以这几条用例盯的是那一遍会不会把撤掉
的席位插回来、会不会把四席收成两席。列的值从本次发布起没有代码再写，用例自己按窗口
里那版镜像留下的样子写进去（``_as_the_old_image_left_it``）。
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
from tests.integration.conftest import chat_ws_url, session_auth_headers

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "f1a9c3e07b42_a_private_chats_two_seats_live_in_the_roster.py"
_STAND_INS = _VERSIONS / "d5c48f1a6b73_an_agent_signs_with_its_instance_handle.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load("_two_seats", _MIGRATION)


def _stand_ins():
    return _load("_agent_signs", _STAND_INS)


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


def _seats(client, topic_id: str) -> tuple[str, str] | None:
    async def _run() -> tuple[str, str] | None:
        async with client.test_factory() as session:
            return await TopicMemberService(session).private_seats(uuid.UUID(topic_id))

    return asyncio.run(_run())


def _who_answers(client, topic_id: str) -> str:
    """这一轮没人被 @ 的时候，答这间房的是谁，它的 handle。"""

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
    """名册上直接加一行。私聊在队友有自己的席位之前就坐着房间派生的替身，
    ``POST /topics/{topic_id}/members`` 今天也往私聊里加得进第三个人。"""

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
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")

    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
    assert _who_answers(client, dm) == "reviewer"


def test_personal_memory_in_a_dm_is_authorized_by_the_two_seats(client):
    """个人记忆只在当事人自己的私聊里读写。当事人是谁，名册说了算。"""
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1")

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
    """未读按对面那一席归类：人按 handle，队友按 agent: 前缀。"""
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    with_person = _dm(client, project_id, "user-1", peer_handle="mentor-1")
    with_reviewer = _dm(client, project_id, "user-1", agent_handle="reviewer")
    reviewer_seats = _seats(client, with_reviewer)
    assert reviewer_seats is not None

    _say(client, project_id, with_person, "mentor-1")
    _say(client, project_id, with_reviewer, reviewer_seats[1])

    assert _badge(client, project_id, "user-1") == {"mentor-1": 1, "agent:reviewer": 1}


def test_opening_the_same_dm_again_finds_it_by_its_two_seats(client):
    """再打开是同一间房，认的是名册上的两席。"""
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    with_reviewer = _dm(client, project_id, "user-1", agent_handle="reviewer")
    with_person = _dm(client, project_id, "user-1", peer_handle="mentor-1")

    assert _dm(client, project_id, "user-1", agent_handle="reviewer") == with_reviewer
    # 人对人的那间两边都找得到，谁先开的不影响。
    assert _dm(client, project_id, "mentor-1", peer_handle="user-1") == with_person
    assert with_reviewer != with_person


def test_reopening_a_dm_whose_roster_grew_finds_the_same_room(client):
    """名册多出一席的房间还是这间 DM：再打开给的是它，历史都在里面。

    私聊不进话题树，角标又被两席那道闸滤掉，所以旧那间房在界面上没有别的入口：
    这里另开一间，里面的对话就再也找不回来。多出一席这间房确实答不出对面是谁，
    但那件事由 ``private_seats`` 一处答，它退回项目默认那位，不另开房。
    """
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _say(client, project_id, dm, "user-1")
    _seat_by_hand(client, dm, "mentor-1")
    assert _seats(client, dm) is None

    again = _dm(client, project_id, "user-1", agent_handle="reviewer")

    assert again == dm
    # 答不出对面是谁的那一条退路照走：项目默认那位答这间房。
    assert _who_answers(client, dm) == "cheese"


def _project_member(client, project_id: str, handle: str) -> None:
    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": handle, "role": "member"},
    )
    assert r.status_code == 200, r.text


def _send(client, topic_id: str, content: str, author: str) -> None:
    """人在房间里说一句话（不唤醒芝士）：@ 的通知在这条消息落库时就发出去了。"""
    with client.websocket_connect(chat_ws_url(topic_id, author)) as ws:
        ws.send_json({"type": "message", "content": content, "summon": False})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def _alerts(client, project_id: str, handle: str) -> list[dict]:
    return client.get(
        f"/projects/{project_id}/alerts",
        headers=session_auth_headers(handle),
    ).json()["data"]["data"]


def _texts(client, topic_id: str) -> list[str]:
    return [
        b["content"]
        for b in client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    ]


def test_a_dm_has_no_member_list_even_when_its_roster_is_not_two_seats(client):
    """私聊里的 @ 出不了这间房，席位不齐的时候也一样。

    「这间房有没有名册」和「两席里的人是哪一位」是两个问题。名册解析不到的 @ 只
    是一条 ⚠️；解析得到，正文前 200 字就进了那个人的强提醒
    （``_notify_mentions``），而他不在这间房里。所以席位不齐的时候不能退：答不出
    对面是谁，可以退回项目默认那位；答错「有没有名册」，是把私聊正文发出去。

    席位不齐这件事真实存在：#1380 那次发布的窗口里旧镜像建的私聊一行席位都没有，
    回填还没跑在真数据上；房间被加进第三个人也是同一种。
    """
    project_id = _project(client)
    _project_member(client, project_id, "user-1")
    _project_member(client, project_id, "mentor-1")
    _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    # 名册不是两席了，而 mentor-1 从头到尾不在这间房里。
    _seat_by_hand(client, dm, "intruder-1")
    assert _seats(client, dm) is None, "前提：这间私聊的名册已经不是两席"

    _send(client, dm, "@mentor-1 这段先别说出去", author="user-1")

    assert _alerts(client, project_id, "mentor-1") == []
    # 名册解析不到，@ 原样留在正文里（渲染成「项目成员里没有这个 handle」的 ⚠️）。
    assert "@mentor-1 这段先别说出去" in _texts(client, dm)


def test_a_pairs_own_dm_wins_over_a_room_that_grew_into_the_pair(client):
    """两间房都坐着这两位时，还是两席的那间才是他们的 DM。

    第三个人被加进 A 和队友的那间房之后，A 和他的私聊与那间房都坐着这两位。挑错
    一间，A 和他之间的对话就落在另一间房里，而那间房在界面上没有别的入口。
    """
    project_id = _project(client)
    _add_agent(client, project_id, "reviewer", "评审")
    with_reviewer = _dm(client, project_id, "user-1", agent_handle="reviewer")
    with_mentor = _dm(client, project_id, "user-1", peer_handle="mentor-1")
    assert with_mentor != with_reviewer

    # 有人把 mentor-1 加进了 user-1 与评审的那间房。
    _seat_by_hand(client, with_reviewer, "mentor-1")

    assert _dm(client, project_id, "user-1", peer_handle="mentor-1") == with_mentor


def _as_the_old_image_left_it(client, topic_id: str, owner: str, peer: str) -> None:
    """把两列写成这次发布之前那版镜像留下的样子。

    从本次发布起建私聊只写名册，两列再也没有写点；而 ``only_the_two_parties`` 补席
    位的唯一输入就是这两列。窗口里旧镜像建的那批私聊长的正是这个样子，下面几条用例
    要的也正是那一批。
    """

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text(
                    "UPDATE topics SET private_owner=:o, private_peer=:p WHERE id=:t"
                ),
                {"t": uuid.UUID(topic_id), "o": owner, "p": peer},
            )
            await session.commit()

    asyncio.run(_run())


def _unseat_by_hand(client, topic_id: str, handle: str) -> None:
    """名册上直接删一行。撤席位就是撤授权，换队友就是这么换的。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text(
                    "DELETE FROM topic_memberships"
                    " WHERE topic_id=:t AND member_handle=:h"
                ),
                {"t": uuid.UUID(topic_id), "h": handle},
            )
            await session.commit()

    asyncio.run(_run())


def _stale_peer_column(client, topic_id: str, handle: str) -> None:
    """把 ``private_peer`` 写成名册以外的人。存量私聊里两列和名册对不上，
    长的就是这个样子。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text("UPDATE topics SET private_peer=:h WHERE id=:t"),
                {"t": uuid.UUID(topic_id), "h": handle},
            )
            await session.commit()

    asyncio.run(_run())


def _roster(client, topic_id: str) -> set[str]:
    """名册上现在坐着谁。"""

    async def _run() -> set[str]:
        async with client.test_factory() as session:
            rows = await session.execute(
                sa.text(
                    "SELECT member_handle FROM topic_memberships WHERE topic_id=:t"
                ),
                {"t": uuid.UUID(topic_id)},
            )
            return {row[0] for row in rows}

    return asyncio.run(_run())


def _retire_the_stand_ins(client) -> None:
    """``d5c48f1a6b73`` 的替身退役那一段，原样跑一遍：调的是它自己的
    ``retire_stand_ins``。存量私聊今天的形状是这一条留下来的，照着手拼一个形状就
    可能拼出一个数据里根本不存在的样子，再拿它去证明迁移。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _stand_ins().retire_stand_ins(sync.exec_driver_sql)
            )
            await session.commit()

    asyncio.run(_run())


def _run_the_migration(client) -> None:
    """跑迁移自己的 ``only_the_two_parties``，不是照抄一份 SQL。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            connection = await session.connection()
            await connection.run_sync(
                lambda sync: _migration().only_the_two_parties(sync.exec_driver_sql)
            )
            await session.commit()

    asyncio.run(_run())


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

    # 名册还没有这间房的时候建的私聊：两列有值，席位一行没有。
    _as_the_old_image_left_it(client, dm, "user-1", reviewer_seat)

    async def _age() -> None:
        async with client.test_factory() as session:
            await session.execute(
                sa.text("DELETE FROM topic_memberships WHERE topic_id=:t"),
                {"t": uuid.UUID(dm)},
            )
            await session.commit()
        async with client.test_factory() as session:
            members = TopicMemberService(session)
            assert await members.private_seats(uuid.UUID(dm)) is None

    asyncio.run(_age())
    _run_the_migration(client)

    assert _seats(client, dm) == ("user-1", reviewer_seat)
    assert _who_answers(client, dm) == "reviewer"

    # 幂等：再跑一遍不多一行。
    _run_the_migration(client)

    async def _count() -> int:
        async with client.test_factory() as session:
            return int(
                await session.scalar(
                    sa.text("SELECT count(*) FROM topic_memberships WHERE topic_id=:t"),
                    {"t": uuid.UUID(dm)},
                )
            )

    assert asyncio.run(_count()) == 2


def test_an_old_dms_extra_seats_are_unseated(client):
    """存量私聊：迁移跑完回到两席，这间房又答得出对面是谁。

    形状不手拼，让 ``d5c48f1a6b73`` 自己跑出来：房里坐着评审，它说不出替身站的是
    哪一个，于是留下替身、又照样补上项目芝士那一席，四席就是这么来的。手拼一个
    三席的样子，拼出来的可能是数据里根本不存在的形状，用例绿而存量坏。

    迁移之前它不是私聊，角标里就不该有它：既不翻倍，也不多出一行归给别人。
    """
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _say(client, project_id, dm, reviewer["seat_handle"])
    assert _badge(client, project_id, "user-1") == {"agent:reviewer": 1}

    # 队友有自己的席位之前，私聊名册上坐的是房间派生的那个替身。
    stand_in = f"cheese-{uuid.UUID(dm).hex[:12]}"
    _seat_by_hand(client, dm, stand_in)
    _retire_the_stand_ins(client)

    roster = _roster(client, dm)
    assert stand_in in roster, "房里坐着评审，d5c48f1a6b73 留下了替身"
    assert len(roster) == 4, f"[人, 队友, 替身, 项目芝士] 四席，实际 {roster}"
    assert _seats(client, dm) is None
    assert _badge(client, project_id, "user-1") == {}

    _run_the_migration(client)

    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
    assert _badge(client, project_id, "user-1") == {"agent:reviewer": 1}
    assert _who_answers(client, dm) == "reviewer"

    # 幂等：再跑一遍不动任何一行。
    _run_the_migration(client)
    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])


def test_an_old_dm_gets_its_human_back_after_the_extra_seats_go(client):
    """人那一席从来没写过的存量私聊：多出来的席位拿掉之后，补种这一步还得跑得到。

    补种的闸是「名册还不到两席」，所以它数的必须是删完之后的名册。反过来跑，这间
    房在删之前是三席，闸把它挡在外面，删完只剩队友一位，人再也补不回来。
    """
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    # 名册还没有人这一席的年代建的私聊，人只在 ``private_owner`` 那一列里。
    _as_the_old_image_left_it(client, dm, "user-1", reviewer["seat_handle"])
    _unseat_by_hand(client, dm, "user-1")
    _seat_by_hand(client, dm, f"cheese-{uuid.UUID(dm).hex[:12]}")
    _retire_the_stand_ins(client)

    _run_the_migration(client)

    assert _seats(client, dm) == ("user-1", reviewer["seat_handle"])
    assert _who_answers(client, dm) == "reviewer"


def test_a_swapped_teammate_is_not_seated_back_by_the_migration(client):
    """换过队友的私聊：迁移不把撤掉的那一席补回来，也不把在用的那一席删掉。

    席位就是授权（``holds_an_agent_seat``），而加席位、换席位、撤席位改的只有名册
    那一份。两列停在上一个队友身上的时候，对的是名册。
    """
    project_id = _project(client)
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    writer = _add_agent(client, project_id, "writer", "写手")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    _as_the_old_image_left_it(client, dm, "user-1", reviewer["seat_handle"])

    # 评审那一席被撤掉，换成写手；``private_peer`` 还停在评审身上。
    _unseat_by_hand(client, dm, reviewer["seat_handle"])
    _seat_by_hand(client, dm, writer["seat_handle"])
    assert _seats(client, dm) == ("user-1", writer["seat_handle"])

    _run_the_migration(client)

    assert _seats(client, dm) == ("user-1", writer["seat_handle"])
    assert _who_answers(client, dm) == "writer"


def test_a_retired_stand_in_is_not_seated_back_by_the_migration(client):
    """替身退役过的私聊：迁移不把它种回名册，也不挤掉项目芝士那一席。

    ``e7d2b91a4c06`` 的第三种情况把房间派生的替身写进了 ``private_peer``（当时
    项目还没有默认芝士），而 ``d5c48f1a6b73`` 后来补上项目芝士的席位、退役了替身。
    照两列判就会把那条迁移整个倒过来。
    """
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1")
    seated = _seats(client, dm)
    assert seated is not None
    _stale_peer_column(client, dm, f"cheese-{uuid.UUID(dm).hex[:12]}")

    _run_the_migration(client)

    assert _seats(client, dm) == seated
    assert _who_answers(client, dm) == "cheese"
