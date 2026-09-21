"""待我处理：跨项目筛出点到我的那些事项。

手机底栏那一格此前接的是顶栏铃铛的通知数据（提及与回复）。通知是一条条事件记录，
不是状态：一张验收卡递上来发过一条通知，之后它被驳回、被作废、或者别人先处理掉了，
那条记录还在，而从它身上读不出它已经不作数。所以这个清单从当下的事实重新算一遍。

规则和看板同一份（`room_task/presentation.py`），只是范围换成我能看见的全部项目，
并在算完之后按「这件事点的是谁」过滤 —— 所以这里钉两件事：**进来的都是待处理**，
**进来的都点到了我**。
"""

import uuid

from app.domain.room_task.models import Task
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.turn_log import open_turn


def _project(client, handle: str) -> str:
    return client.post(
        "/projects", json={"name": "P"}, headers=session_auth_headers(handle)
    ).json()["data"]["id"]


def _room(client, project: str, handle: str, title: str = "预算复核") -> str:
    return client.post(
        "/topics",
        json={"project_id": project, "title": title},
        headers=session_auth_headers(handle),
    ).json()["data"]["id"]


def _file_card(client, room: str, reviewer: str) -> None:
    r = client.post(
        f"/topics/{room}/tasks/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "chore(test): file a card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200, r.text


def _set_reporter(client, room: str, handle: str) -> None:
    task = delivery_task(client, room)

    async def go() -> None:
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.reporter_handle = handle
            await session.commit()

    client.portal.call(go)


def _ask(client, room: str, handle: str) -> None:
    r = client.post(
        f"/topics/{room}/ask",
        json={"question": "预算按哪个口径统计", "options": ["按部门", "按项目"]},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text


def _mine(client, handle: str) -> list[dict]:
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_a_card_routed_to_me_is_on_my_list_and_not_on_anyone_elses(client):
    project = _project(client, "alice")
    room = _room(client, project, "alice")

    _file_card(client, room, reviewer="alice")

    (item,) = _mine(client, "alice")
    assert item["topicId"] == room
    assert item["topicTitle"] == "预算复核"
    assert item["displayStatus"] == "等待验收"
    assert item["reason"] == "reviewer"
    assert _mine(client, "bob") == []


def test_the_person_who_asked_for_the_work_is_on_it_too(client):
    """提需求的人等的东西有了结果 —— 他和验收人一起进清单。"""
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    _set_reporter(client, room, "alice")

    _file_card(client, room, reviewer="carol")

    (item,) = _mine(client, "alice")
    assert item["reason"] == "reporter"


def test_a_question_is_only_on_the_list_of_whoever_started_the_turn(client):
    """一个待确认问题只有发起那一轮的人能回答。

    凭房间名册推收件人，等于把一条只有一个人该处理的事项摆进一屋子人的待办里。
    """
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    client.portal.call(
        lambda: open_turn(client.test_factory, uuid.UUID(room), author="alice")
    )

    _ask(client, room, "alice")

    (item,) = _mine(client, "alice")
    assert item["displayStatus"] == "待确认"
    assert item["reason"] == "asked"
    assert item["taskId"] is None  # 房间自己那条线上的提问


def test_the_newest_open_turn_decides_who_the_question_is_waiting_on(client):
    """一个房间可能留着不止一个没关的轮次 —— 取最近开始的那一个。

    扫底没来得及关掉的旧轮次不该替新的那一轮回答「这在等谁」：那会把事项摆到一个
    早就走开的人的清单里，而真正在等的人什么都看不到。
    """
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    client.portal.call(
        lambda: open_turn(
            client.test_factory, uuid.UUID(room), author="bob", age_s=3600
        )
    )
    client.portal.call(
        lambda: open_turn(client.test_factory, uuid.UUID(room), author="alice", age_s=5)
    )

    _ask(client, room, "alice")

    (item,) = _mine(client, "alice")
    assert item["reason"] == "asked"
    assert _mine(client, "bob") == []


def test_a_question_in_a_platform_turn_is_on_nobody_s_list(client):
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    client.portal.call(
        lambda: open_turn(client.test_factory, uuid.UUID(room), author="system")
    )

    _ask(client, room, "alice")

    assert _mine(client, "alice") == []


def test_an_idle_room_is_not_something_to_process(client):
    """清单只收待处理 —— 一个闲着的房间没有在等任何人。"""
    project = _project(client, "alice")
    _room(client, project, "alice")

    assert _mine(client, "alice") == []


def test_a_visitor_has_nothing_to_process(client):
    """没有身份的调用者没有被任何一件事点到 —— 空列表，不是错误。"""
    r = client.get("/awaiting-me")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["data"] == []
