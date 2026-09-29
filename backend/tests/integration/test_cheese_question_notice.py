"""芝士提出待回答的问题后本轮停止等待 —— 看板要显示它，等回答的人要收到通知。

这是「下一步在人手上」里唯一**会中断运行**的一种：其余几种都是一轮结束之后的状态
（验收卡已提交、检查未通过、已退回），而一个待回答的问题把这一轮停在中途。中断
本身在界面上没有任何痕迹 —— 房间只是安静下来，而安静与正在运行无法区分。

所以两件事一起做：房间与任务进「待处理 · 待回答」，同时通知发起这一轮的人。芝士
是代他执行这件事的，这个问题也只有他能回答。

判据是 #1084 定的那一条，不新增存储：**最近一条提问消息没有 `answered`**，且此后
没人给过回应。回应有三条出路：被问的人点了选项（`answered`）、他直接打字回了一句、
或者**芝士自己又接着说了一句**（#2046：芝士问完没等人答就自己把活做完又发了几条
进展，房间却一直停在「待回答」）。第三条只管芝士自己问出口的题 —— 人问的那道题，
芝士在不在房间里说话都与它无关。
"""

import uuid

from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.room_task.presentation import NeedsYou
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.delivery import delivery_headers
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.turn_log import open_turn


def _room(client) -> tuple[str, str]:
    """alice 创建的房间 —— 创建者即名册上的第一个人，而提问要求调用者在名册内。"""
    auth = session_auth_headers("alice")
    pid = post_project(client, json={"name": "P"}, headers=auth).json()["data"]["id"]
    tid = client.post(
        "/topics", json={"project_id": pid, "title": "预算复核"}, headers=auth
    ).json()["data"]["id"]
    return pid, tid


def _ask(client, room: str, question: str = "预算按哪个口径统计") -> str:
    r = client.post(
        f"/topics/{room}/ask",
        json={"question": question, "options": ["按部门", "按项目"]},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _open_turn(client, room: str, handle: str = "alice"):
    return client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(room), author=handle)
    )


def _agent_ask(client, room: str) -> str:
    """芝士自己问出口的那道题（`cheese_ask`）—— 署名是房间里的那个席位。"""
    r = client.post(
        f"/topics/{room}/ask",
        json={
            "question": "截图里那个灰底圆角块是哪一处？",
            "options": ["左边那行", "顶上那行"],
        },
        headers=delivery_headers(client, room),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _agent_says(client, room: str, text: str) -> None:
    """芝士在房间自己的线上又发了一句 —— 落一条真消息，不走轮次。"""
    agent = room_agent_seat(client, room)

    async def go() -> None:
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room))
            await BlockRepository(session).add(
                project_id=topic.project_id,
                topic_id=topic.id,
                author=agent,
                author_type=AuthorType.participant,
                content=text,
            )
            await session.commit()

    client.portal.call(go)


def _answer(client, block_id: str, option: str = "按部门") -> None:
    r = client.post(
        f"/topics/blocks/{block_id}/answer",
        json={"option": option, "author": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _shown(client, project: str, room: str) -> dict:
    rows = client.get("/topics", params={"project_id": project}).json()["data"]["data"]
    (row,) = [r for r in rows if r["id"] == room]
    return row["presentation"]


def _questions(client, token: str) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "CHEESE_QUESTION"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def test_an_unanswered_question_puts_the_room_in_the_waiting_column(client):
    seed_user(client, "alice")
    pid, room = _room(client)

    _ask(client, room)

    shown = _shown(client, pid, room)
    assert shown["column"] == "needs_you"
    assert shown["display_status"] == NeedsYou.awaiting_answer


def test_answering_it_takes_the_room_back_out(client):
    """已回答的问题不应让房间长期停留在待回答 —— 判据是**最近一条**。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    block = _ask(client, room)
    assert _shown(client, pid, room)["column"] == "needs_you"

    _answer(client, block)

    assert _shown(client, pid, room)["column"] != "needs_you"


def test_a_second_question_after_an_answered_one_still_counts(client):
    """回答之后又有新提问 —— 取最近一条，所以仍然停在待回答。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    _answer(client, _ask(client, room))

    _ask(client, room, "那按项目的口径要不要含外包")

    assert _shown(client, pid, room)["display_status"] == NeedsYou.awaiting_answer


def test_an_agent_that_speaks_again_takes_its_own_question_off_the_desk(client):
    """芝士问完没等回答，自己又接着说了几句 —— 那道题不再挂在人身上 (#2046)。

    实况：芝士在房间里问「截图里那个灰底圆角块是哪一处」，没等人答就自己找到根因、
    把活做完、又发了几条进展，而房间从 01:36 一直停在「待回答」，直到人真去点一下
    才灭。提问的人自己往前走了，球就不在他手上了。
    """
    seed_user(client, "alice")
    pid, room = _room(client)
    _open_turn(client, room)

    _agent_ask(client, room)
    assert _shown(client, pid, room)["display_status"] == NeedsYou.awaiting_answer

    _agent_says(client, room, "找到根因了，改完推上去了")

    assert _shown(client, pid, room)["display_status"] != NeedsYou.awaiting_answer


def test_a_question_a_person_asked_still_waits_while_the_agent_works(client):
    """这条只管芝士自己问的题：人问的题不会因为芝士在房间里说话而消失。

    人问完那一句，要答的还是他；芝士在旁边干活不是他的回答。
    """
    seed_user(client, "alice")
    pid, room = _room(client)
    _open_turn(client, room)

    _ask(client, room)
    _agent_says(client, room, "我先把能查的查了")

    assert _shown(client, pid, room)["display_status"] == NeedsYou.awaiting_answer


def test_the_person_who_started_the_turn_hears_the_question(client):
    """收件人是发起这一轮的人，不是房间名册。

    芝士是代他执行这件事的，房间里其他人并不在等这个回答。
    """
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    _pid, room = _room(client)
    client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(room), author="bob")
    )

    _ask(client, room)

    (row,) = _questions(client, bob)
    assert row["contextMetadata"]["question"] == "预算按哪个口径统计"
    assert row["contextMetadata"]["topicId"] == room
    assert row["contextMetadata"]["topicTitle"] == "预算复核"
    assert row["read"] is False
    assert _questions(client, alice) == []  # 未发起这一轮的人不接收


def test_a_question_in_a_turn_the_platform_started_reaches_nobody(client):
    """平台发起的轮次（resume、各类提醒）作者是 system。

    这种轮次里的提问指不到具体的人，因此不通知任何人：把一条多数人不需要处理的
    通知发给全部成员，代价是他们此后关闭这个渠道。
    """
    alice = seed_user(client, "alice")
    _pid, room = _room(client)
    client.portal.call(
        lambda: open_turn(client.test_request_factory, uuid.UUID(room), author="system")
    )

    _ask(client, room)

    assert _questions(client, alice) == []


def test_a_question_with_no_turn_at_all_reaches_nobody(client):
    """没有进行中的轮次 —— 没有人在等这个回答，因此不通知任何人。"""
    alice = seed_user(client, "alice")
    _pid, room = _room(client)

    _ask(client, room)

    assert _questions(client, alice) == []
