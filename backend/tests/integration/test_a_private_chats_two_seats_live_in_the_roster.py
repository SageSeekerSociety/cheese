"""私聊的两席在名册上：「谁被点名」由此推出，不需要 @（结论 19、20）。

一间私聊就是项目内名册两席的房间。所以「对面是谁」只有名册一个出处：谁答这间房、
个人记忆记在谁名下、未读按谁归类、再打开是不是同一间，四个问题问的都是这两席。

名册是唯一的出处：同一件事没有第二个地方记着，所以这里的每一条断言都只能是名册答
出来的。
"""

import asyncio
import uuid

import sqlalchemy as sa

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(client, json={"name": "Demo"}).json()["data"]["id"]


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


def _project_with_a_roster(client, owner: str, member: str) -> str:
    """一个真有名册的项目：所有者，加一位成员。@ 要解析得到人，名册里就得有人。"""
    project_id = post_project(
        client, json={"name": "Demo", "owner_handle": owner}
    ).json()["data"]["id"]
    join_project_team(client, project_id, member)
    return project_id


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


def _texts(client, topic_id: str, reader: str) -> list[str]:
    r = client.get(f"/topics/{topic_id}/blocks", headers=session_auth_headers(reader))
    assert r.status_code == 200, r.text
    return [b["content"] for b in r.json()["data"]["data"]]


def test_a_dm_has_no_member_list_even_when_its_roster_is_not_two_seats(client):
    """私聊里的 @ 出不了这间房，席位不齐的时候也一样。

    「这间房有没有名册」和「两席里的人是哪一位」是两个问题。名册解析不到的 @ 只
    是一条 ⚠️；解析得到，正文前 200 字就进了那个人的强提醒
    （``_notify_mentions``），而他不在这间房里。所以席位不齐的时候不能退：答不出
    对面是谁，可以退回项目默认那位；答错「有没有名册」，是把私聊正文发出去。

    席位不齐这件事真实存在：#1380 那次发布的窗口里旧镜像建的私聊一行席位都没有，
    回填还没跑在真数据上；房间被加进第三个人也是同一种。
    """
    project_id = _project_with_a_roster(client, owner="user-1", member="mentor-1")
    _add_agent(client, project_id, "reviewer", "评审")
    dm = _dm(client, project_id, "user-1", agent_handle="reviewer")
    # 名册不是两席了，而 mentor-1 从头到尾不在这间房里。
    _seat_by_hand(client, dm, "intruder-1")
    assert _seats(client, dm) is None, "前提：这间私聊的名册已经不是两席"

    _send(client, dm, "@mentor-1 这段先别说出去", author="user-1")

    assert _alerts(client, project_id, "mentor-1") == []
    # 名册解析不到，@ 原样留在正文里（渲染成「项目成员里没有这个 handle」的 ⚠️）。
    assert "@mentor-1 这段先别说出去" in _texts(client, dm, "user-1")


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
