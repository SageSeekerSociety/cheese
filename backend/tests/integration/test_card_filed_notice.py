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

from app.domain.addressing import Event
from app.domain.agent.announce import announce
from app.domain.notice_meta import (
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.room_task.models import Task
from tests.conftest import seed_user, wait_work_idle
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import (
    as_read,
    join_project_team,
    mail,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept_pr import _accept as _accept_with_a_pr
from tests.integration.test_accept_pr import _ready_card, app_world  # noqa: F401

_SUBJECT = "chore(test): file an accept card"


def _room_and_project(client) -> tuple[str, str]:
    """`_room`, plus the id of the project the room lives in — for a test that
    also has to put the card's reviewer on the project roster."""
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    # alice is the reviewer these cards are routed to, and the person who
    # rejects one below. A card decision requires room membership since
    # 2026-09-26 (`_card_actor`), so she is a participant of the project.
    join_project_team(client, pid, "alice")
    room = client.post("/topics", json={"project_id": pid, "title": "预算复核"}).json()[
        "data"
    ]["id"]
    return pid, room


def _room(client) -> str:
    return _room_and_project(client)[1]


def _set_reporter(client, room: str, handle: str) -> None:
    """把这条活的递卡人写成 ``handle``。

    递卡人还要是这个项目的人：撤回只认递卡人（`_may_withdraw`），而每个决定
    验收卡的路由都先问「你是这个频道的人吗」（`_card_actor`）—— 只在库里写个
    名字，他连自己那张卡都退不了。
    """
    task = delivery_task(client, room)
    join_project_team(client, str(task.project_id), handle)

    async def go() -> None:
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.reporter_handle = handle
            await session.commit()

    client.portal.call(go)


def _file_card(client, room: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": _SUBJECT,
            "reviewer_handle": reviewer,
            "focus": "最懂",
        },
    )


def _filed_events(client, room: str) -> list[dict]:
    blocks = client.get(f"/topics/{delivery_task_id(client, room)}/blocks").json()[
        "data"
    ]["data"]
    return [
        b
        for b in blocks
        if b["kind"] == "event"
        and (b.get("meta") or {}).get("event_type") == "card_filed"
    ]


def _notices(client, token: str) -> list[dict]:
    return mail(client, token, "ROOM_NOTICE")


def test_filing_a_card_leaves_a_room_line_marked_as_waiting_on_a_person(client):
    seed_user(client, "alice")
    room = _room(client)

    assert _file_card(client, room).status_code == 200

    (event,) = _filed_events(client, room)
    assert "alice" in event["content"]
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
        assert row["contextMetadata"]["content"] == as_read(event["content"])
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

    blocks = client.get(f"/topics/{delivery_task_id(client, room)}/blocks").json()[
        "data"
    ]["data"]
    assert any(
        (b.get("meta") or {}).get("event_type") == "card_rejected" for b in blocks
    )
    assert len(_notices(client, alice)) == 1


# ===================== 卡一有决定，「等你」那几句就收掉 =====================


def _unread(client, token: str) -> list[dict]:
    return [row for row in _notices(client, token) if row["read"] is False]


def _decide(client, card: str, action: str, who: str = "alice", **body):
    # 谁在决定有两条通道：认证（路由读 `actor.handle`，只有它算数）与请求体里那一格
    # （`RejectDecision.decided_by` 必填，但值不被采纳）。两处写同一个人，所以要换人
    # 就换这里 —— 光改请求体不换认证，判据看的还是认证出来的那个人。
    body.setdefault("decided_by", who)
    return client.post(
        f"/accept-cards/{card}/{action}",
        json=body,
        headers=session_auth_headers(who),
    )


def test_rejecting_the_card_clears_the_wait_from_both_inboxes(client):
    """驳回之后，那两句「等你」不该还躺在谁的收件箱里。

    递卡投出去的那一条说的是「待你审阅」；卡一旦有了结局，它就不成立了 —— 可通
    知是一条事件记录，它会一直留到人自己去点「已读」。所以决定的那几处按**卡号**
    把这类提示一起结掉（`about_card` → `ledger.settle_card`），标成已读并记下结
    局。结的是已读，不是删掉：那句话还查得到，只是不再等人。
    """
    alice = seed_user(client, "alice")
    reporter = seed_user(client, "bob")
    room = _room(client)
    _set_reporter(client, room, "bob")
    card = _file_card(client, room).json()["data"]["id"]
    assert len(_unread(client, alice)) == 1
    assert len(_unread(client, reporter)) == 1

    # 退回只认这张卡的审阅人：alice 是递卡时点名的那一位，所以由她退回。
    r = _decide(client, card, "reject", who="alice", note="口径和上一版对不上")
    assert r.status_code == 200, r.text
    wait_work_idle()

    for token in (alice, reporter):
        (row,) = _notices(client, token)
        assert row["read"] is True
        assert row["contextMetadata"]["outcome"] == "rejected"


def test_accepting_the_card_clears_the_wait_from_the_reviewers_inbox(client, request):
    """走完整的 GitHub 那条路：点采纳、合并、卡落到已采纳，提醒跟着收掉。"""
    # 借 `test_accept_pr` 的那套世界：真项目 + 假 GitHub + 卡骑着 PR。
    world = request.getfixturevalue("app_world")
    alice = seed_user(client, "alice")
    _, _, card, _, head = _ready_card(client, world)
    assert len(_unread(client, alice)) == 1

    world["fake"].check_state_by_sha[head] = ("success", "green")
    r = _accept_with_a_pr(client, card)
    assert r.status_code == 200, r.text
    wait_work_idle()

    (row,) = _notices(client, alice)
    assert row["read"] is True
    assert row["contextMetadata"]["outcome"] == "accepted"


def test_only_the_decided_card_s_wait_is_cleared(client):
    """按卡号结，不按房间结：另一个房间那张卡还等着人动。

    两条提示的收件人相同、房间不同 —— 分得开它们的只有卡号。
    """
    alice = seed_user(client, "alice")
    pid, room = _room_and_project(client)
    other = client.post(
        "/topics", json={"project_id": pid, "title": "另一件事"}
    ).json()["data"]["id"]
    card = _file_card(client, room).json()["data"]["id"]
    assert _file_card(client, other).status_code == 200
    assert len(_unread(client, alice)) == 2

    r = _decide(client, card, "reject", decided_by="alice", note="重来")
    assert r.status_code == 200, r.text
    wait_work_idle()

    (left,) = _unread(client, alice)
    assert left["contextMetadata"]["topicId"] == other


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
