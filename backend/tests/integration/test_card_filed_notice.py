"""递卡说一声，并通知等着的那两个人。

卡的每一种结局在房间里都有一行 —— 驳回、作废、改描述、合了、卡住了。唯独等待的
**开始**没有：验收卡钉在对话末尾，不随时间线往上滚，所以翻历史也看不出它是什么
时候递上来的。而验收人这会儿可能根本没打开这个房间。

所以递卡落一行 `card_filed`，并把同一句话投给两个人：验收人（这件事现在在他手
上）和提需求的人（他等的东西有结果了）。通知里的文字就是房间里那一行 —— 两处对
同一件事只有一种说法。
"""

import uuid

import pytest

from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.delivery.addressing import Event
from app.domain.identity.handles import topic_agent_handle
from app.domain.room_task.models import Task
from tests.conftest import seed_user, wait_work_idle
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import session_auth_headers

_SUBJECT = "chore(test): file an accept card"


def _room(client) -> str:
    pid = client.post("/projects", json={"name": "P"}).json()["data"]["id"]
    return client.post("/topics", json={"project_id": pid, "title": "预算复核"}).json()[
        "data"
    ]["id"]


def _set_reporter(client, room: str, handle: str) -> None:
    task = delivery_task(client, room)

    async def go() -> None:
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.reporter_handle = handle
            await session.commit()

    client.portal.call(go)


def _file_card(client, room: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{room}/tasks/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "new_artifact": "报告",
            "change_subject": _SUBJECT,
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def _filed_events(client, room: str) -> list[dict]:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return [
        b
        for b in blocks
        if b["kind"] == "event"
        and (b.get("meta") or {}).get("event_type") == "card_filed"
    ]


def _notices(client, token: str) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "ROOM_NOTICE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def test_filing_a_card_leaves_a_room_line_marked_as_waiting_on_a_person(client):
    seed_user(client, "alice")
    room = _room(client)

    assert _file_card(client, room).status_code == 200

    (event,) = _filed_events(client, room)
    assert "待 alice 验收" in event["content"]
    assert event["meta"]["who"] == "human"
    # 改动主题最长 72 字，房间里那一行要保持一行 —— 所以它进展开区，不进正文。
    assert _SUBJECT in event["meta"]["detail"]
    assert _SUBJECT not in event["content"]


def test_the_reviewer_and_the_reporter_both_hear_about_it(client):
    reviewer = seed_user(client, "alice")
    reporter = seed_user(client, "bob")
    room = _room(client)
    _set_reporter(client, room, "bob")

    assert _file_card(client, room, reviewer="alice").status_code == 200

    (event,) = _filed_events(client, room)
    for token in (reviewer, reporter):
        (row,) = _notices(client, token)
        # 通知里读到的和回房间看到的是同一句。
        assert row["contextMetadata"]["content"] == event["content"]
        assert row["contextMetadata"]["topicTitle"] == "预算复核"
        assert row["contextMetadata"]["topicId"] == room
        assert row["read"] is False


def test_one_person_wearing_both_hats_hears_about_it_once(client):
    alice = seed_user(client, "alice")
    room = _room(client)
    _set_reporter(client, room, "alice")

    assert _file_card(client, room, reviewer="alice").status_code == 200

    assert len(_notices(client, alice)) == 1


def test_an_agent_reviewer_reads_it_in_the_room_instead_of_the_mailbox(client):
    """卡递给 agent：房间里那一行照落，提需求的人照收，agent 的收件箱是空的。

    agent 有用户行，handle 解析得出用户 id，所以它以前照样收到一条站内信 —— 一条
    谁都不会打开的记录，没有报错也没有人看得见。同一条事件对人和 agent 说的是同一
    句话（谁该收到），分岔只在怎么送到（`identity/arrival.py`）。
    """
    room = _room(client)
    agent = topic_agent_handle(uuid.UUID(room))
    agent_token = seed_user(client, agent)
    reporter = seed_user(client, "bob")
    _set_reporter(client, room, "bob")

    assert _file_card(client, room, reviewer=agent).status_code == 200

    (event,) = _filed_events(client, room)
    assert f"待 {agent} 验收" in event["content"]
    (row,) = _notices(client, reporter)
    assert row["contextMetadata"]["content"] == event["content"]
    assert _notices(client, agent_token) == []


def test_a_notice_that_names_nobody_reaches_nobody(client):
    """驳回同样在房间里留一行，但它没点名收件人，所以谁的铃也不响。

    「要人来」只说了要人，没说要哪个人。不点名的提示只在房间里留话。
    """
    alice = seed_user(client, "alice")
    room = _room(client)
    card = _file_card(client, room).json()["data"]["id"]
    assert len(_notices(client, alice)) == 1

    r = client.post(
        f"/accept-cards/{card}/reject",
        json={"decided_by": "alice", "note": "口径和上一版对不上"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    assert any(
        (b.get("meta") or {}).get("event_type") == "card_rejected" for b in blocks
    )
    assert len(_notices(client, alice)) == 1


@pytest.mark.parametrize(
    "who,told",
    [(WHO_PLATFORM, False), (WHO_CHEESE, False), (WHO_HUMAN, True)],
)
def test_who_is_handling_it_decides_whether_the_named_person_hears(client, who, told):
    """同一条事件、同一个被点名的人，只有「等人」那一档发得出去。

    平台或芝士正在处理的事发一条通知出去，等于把一条不需要任何人动手的消息推到别
    人面前。调用点说的是这条事件点了谁的名；下一步在谁手上是 `who` 说的，所以
    「平台在处理，另外通知这几个人」在调用点那里根本写不出来。房间里那一行三档都
    照落 —— 不通知不等于不留话。
    """
    alice = seed_user(client, "alice")
    room = _room(client)

    async def go() -> None:
        async with client.test_factory() as session:
            await announce(
                session,
                place_id=uuid.UUID(room),
                content="这轮在排队",
                meta=notice(EVENT_TURN_QUEUED, severity=SEVERITY_INFO, who=who),
                points_at=Event(reviewers=("alice",)),
            )
            await session.commit()

    client.portal.call(go)

    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    assert any((b.get("meta") or {}).get("event_type") == "turn_queued" for b in blocks)
    assert len(_notices(client, alice)) == (1 if told else 0)
