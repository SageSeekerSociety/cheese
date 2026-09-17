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
    WHO_PLATFORM,
    notice,
)
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


def test_a_notice_the_platform_is_handling_cannot_name_recipients(client):
    """收件人只有 who=human 能点。

    另外两个码说的是平台或芝士正在处理 —— 那种事发一条通知出去，等于把一条不需要
    任何人动手的消息推到别人面前。写错了要当场炸，而不是静悄悄多发一条。
    """
    seed_user(client, "alice")
    room = _room(client)

    async def go() -> None:
        async with client.test_factory() as session:
            with pytest.raises(ValueError):
                await announce(
                    session,
                    place_id=uuid.UUID(room),
                    content="这轮在排队",
                    meta=notice(
                        EVENT_TURN_QUEUED,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                    ),
                    recipients=("alice",),
                )

    client.portal.call(go)
