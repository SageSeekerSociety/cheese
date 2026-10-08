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
from app.domain.room_task.presentation import NeedsYou
from tests.ask_fixtures import active_ask
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.turn_log import open_turn


def _project(client, handle: str) -> str:
    return post_project(
        client, json={"name": "P"}, headers=session_auth_headers(handle)
    ).json()["data"]["id"]


def _room(client, project: str, handle: str, title: str = "预算复核") -> str:
    return client.post(
        "/topics",
        json={"project_id": project, "title": title},
        headers=session_auth_headers(handle),
    ).json()["data"]["id"]


def _file_card(client, room: str, reviewer: str) -> None:
    r = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "chore(test): file a card",
            "reviewer_handle": reviewer,
            "focus": "最懂",
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


def _ask(client, room: str, headers: dict[str, str], *, request_id=None) -> None:
    """芝士在这一轮里问出口的题 —— 凭据是这轮自己的那位队友。"""
    r = client.post(
        f"/topics/{room}/asks",
        json={
            **({"request_id": request_id} if request_id is not None else {}),
            "questions": [
                {
                    "question": "预算按哪个口径统计",
                    "options": [{"text": "按部门"}, {"text": "按项目"}],
                }
            ],
        },
        headers=headers,
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
    assert item["phrase"] == NeedsYou.awaiting_review
    assert item["reason"] == "reviewer"
    assert _mine(client, "bob") == []


def test_the_person_who_asked_for_the_work_is_on_it_too(client):
    """提需求的人等的东西有了结果 —— 他和验收人一起进清单。"""
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    _set_reporter(client, room, "alice")
    # 2026-09-27: 递卡那道门现在先问「这个人在不在房间里」
    # (`_require_reviewer_in_room`)。这张卡递给 carol（提需求的 alice 与验收人必须
    # 是两个人，否则量到的不是「提需求的人也进清单」），就让她真在项目里。
    join_project_team(client, project, "carol")

    _file_card(client, room, reviewer="carol")

    (item,) = _mine(client, "alice")
    assert item["reason"] == "reporter"


def test_a_question_is_only_on_the_list_of_whoever_started_the_turn(
    client, stub_hooks, monkeypatch
):
    """一个待回答的问题只有发起那一轮的人能回答。

    凭房间名册推收件人，等于把一条只有一个人该处理的事项摆进一屋子人的待办里。
    """
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers)
        (item,) = _mine(client, "alice")
        assert item["phrase"] == NeedsYou.awaiting_answer
        assert item["reason"] == "asked"
        assert item["taskId"] is None  # 房间自己那条线上的提问


def test_a_question_still_waits_on_its_person_after_the_turn_ends(
    client, stub_hooks, monkeypatch
):
    """`cheese_ask` 不等回答：芝士问完就收尾，这一轮随即关闭。

    题还摆在那儿，等的还是那个人——「在等谁」不能在轮次关掉的那一刻跟着消失。
    """
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers)

    (item,) = _mine(client, "alice")
    assert item["reason"] == "asked"


def test_a_leftover_turn_does_not_decide_who_the_question_waits_on(
    client, stub_hooks, monkeypatch
):
    """扫底没关掉的旧轮次不该替正在跑的这一轮回答「这在等谁」。

    那会把事项摆到一个早就走开的人的清单里，而真正在等的人什么都看不到。
    现在「在等谁」问的是在跑的这一轮：题问出口那一刻记在题上（`meta.asked`），
    旧轮次留下的记录连提问都影响不了 —— 状态对不上时提问直接被拒，不猜人。
    """
    project = _project(client, "alice")
    room = _room(client, project, "alice")
    # 芝士在支线里回答，题也在那里问。
    thread = in_thread(client, room, "alice")
    client.portal.call(
        lambda: open_turn(
            client.test_factory, uuid.UUID(thread), author="bob", age_s=3600
        )
    )

    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice") as headers:
        _ask(client, thread, headers)

    (item,) = _mine(client, "alice")
    assert item["reason"] == "asked"
    assert _mine(client, "bob") == []


def test_a_question_in_a_platform_turn_is_on_nobody_s_list(
    client, stub_hooks, monkeypatch
):
    project = _project(client, "alice")
    room = _room(client, project, "alice")

    with active_ask(
        client, stub_hooks, monkeypatch, room, actor="alice", platform_turn=True
    ) as headers:
        _ask(client, room, headers)

    assert _mine(client, "alice") == []
    assert _mine(client, "bob") == []


def test_an_idle_room_is_not_something_to_process(client):
    """清单只收待处理 —— 一个闲着的房间没有在等任何人。"""
    project = _project(client, "alice")
    _room(client, project, "alice")

    assert _mine(client, "alice") == []


def test_one_request_id_in_two_rooms_asks_in_both(client, stub_hooks, monkeypatch):
    """A retry key is the asking conversation's: the same one elsewhere is a new
    question."""
    request_id = str(uuid.uuid4())
    project = _project(client, "alice")
    rooms = [_room(client, project, "alice", title) for title in ("预算", "发布")]
    for room in rooms:
        # 芝士在支线里回答，题也在那里问。
        thread = in_thread(client, room, "alice")
        with active_ask(
            client, stub_hooks, monkeypatch, thread, actor="alice"
        ) as headers:
            _ask(client, thread, headers, request_id=request_id)

    questions = [
        item
        for item in _mine(client, "alice")
        if item["phrase"] == NeedsYou.awaiting_answer
    ]
    assert {item["topicId"] for item in questions} == set(rooms)


def test_a_visitor_has_nothing_to_process(client):
    """没有身份的调用者没有被任何一件事点到 —— 空列表，不是错误。"""
    r = client.get("/awaiting-me")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["data"] == []
