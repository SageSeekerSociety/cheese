"""芝士提出待确认问题后本轮停止等待 —— 看板要显示它，等回答的人要收到通知。

这是「下一步在人手上」里唯一**会中断运行**的一种：其余几种都是一轮结束之后的状态
（验收卡已提交、检查未通过、交付被退回），而一个待确认问题把这一轮停在中途。中断
本身在界面上没有任何痕迹 —— 房间只是安静下来，而安静与正在运行无法区分。

所以两件事一起做：房间与任务进「待处理 · 待确认」，同时通知发起这一轮的人。芝士
是代他执行这件事的，这个问题也只有他能回答。

判据是 #1084 定的那一条，不新增存储：**最近一条提问消息没有 `answered`**。
"""

import uuid

from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers
from tests.turn_log import open_turn


def _room(client) -> tuple[str, str]:
    """alice 创建的房间 —— 创建者即名册上的第一个人，而提问要求调用者在名册内。"""
    auth = session_auth_headers("alice")
    pid = client.post("/projects", json={"name": "P"}, headers=auth).json()["data"]["id"]
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
    assert shown["display_status"] == "待确认"


def test_answering_it_takes_the_room_back_out(client):
    """已回答的问题不应让房间长期停留在待确认 —— 判据是**最近一条**。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    block = _ask(client, room)
    assert _shown(client, pid, room)["column"] == "needs_you"

    _answer(client, block)

    assert _shown(client, pid, room)["column"] != "needs_you"


def test_a_second_question_after_an_answered_one_still_counts(client):
    """回答之后又有新提问 —— 取最近一条，所以仍然停在待确认。"""
    seed_user(client, "alice")
    pid, room = _room(client)
    _answer(client, _ask(client, room))

    _ask(client, room, "那按项目的口径要不要含外包")

    assert _shown(client, pid, room)["display_status"] == "待确认"


def test_the_person_who_started_the_turn_hears_the_question(client):
    """收件人是发起这一轮的人，不是房间名册。

    芝士是代他执行这件事的，房间里其他人并不在等这个回答。
    """
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    _pid, room = _room(client)
    client.portal.call(
        lambda: open_turn(client.test_factory, uuid.UUID(room), author="bob")
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
        lambda: open_turn(client.test_factory, uuid.UUID(room), author="system")
    )

    _ask(client, room)

    assert _questions(client, alice) == []


def test_a_question_with_no_turn_at_all_reaches_nobody(client):
    """没有进行中的轮次 —— 没有人在等这个回答，因此不通知任何人。"""
    alice = seed_user(client, "alice")
    _pid, room = _room(client)

    _ask(client, room)

    assert _questions(client, alice) == []
