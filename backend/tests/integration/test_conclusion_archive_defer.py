"""结论采信之后，那条支线收起来 —— 而且不欠任何东西。

这个文件原本钉的是「归档别吃验收卡」（2026-08-16 现场）：支线递了验收卡，父话题
那一轮结束时结论卡默认采信，两分钟后验收卡变成 `revoked`，验收人从头到尾没机会
点。当时的修法是**欠着**：支线还挂着一张等人的卡就先不收起，等卡有了结果再补上
那次归档。

那个场面现在在结构上不存在了：**递卡是房间的事，支线递不了**（一棵树=一个分支=
一个 PR=一批活，封树开 PR 是房间对整批活说的话）。「分身做完 → conclude → 递卡」
这个当初互相踩的组合，后半截已经不允许发生——支线做完只 `conclude`，卡由房间递。
收起一条支线也碰不到房间那张卡，所以没有任何卡会被采信连带作废。

于是「欠着归档」那套机制（`ConclusionService._archive_subtopic` 的 defer 分支、
`sweep_deferred_archives`、`ARCHIVE_DEFERRED` 标记）没有了触发条件。这里测的是
剩下的那条唯一路径：**三条采信的路都把支线收起，一次都不欠**。

全部走外部可观察的行为（卡的状态、话题的状态、卡面的 settle_reason），不看实现。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.conclusion.models import (
    ARCHIVE_REFILE_GRACE_MINUTES,
    ConclusionStatus,
)
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.conclusion.services import ConclusionCardService
from app.domain.review.models import AcceptStatus
from app.domain.topic.services import TopicService
from tests.conftest import wait_work_idle as _wait_work_idle

PAST_GRACE = ARCHIVE_REFILE_GRACE_MINUTES + 1


# --- harness ---------------------------------------------------------------


def _project(client) -> dict:
    return client.post("/projects", json={"name": "P"}).json()["data"]


def _topic(client, project_id: str, title: str = "大话题") -> dict:
    return client.post(
        "/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]


def _split(client, parent_id: str, title: str) -> dict:
    sub = client.post(f"/topics/{parent_id}/split", json={"title": title}).json()[
        "data"
    ]
    # The 分身's auto-kickoff turn must finish before the test writes more.
    _wait_work_idle()
    return sub


def _file_conclusion(client, sub_id: str, conclusion: str = "做完了") -> dict:
    """开一张结论卡，但**不唤醒父话题**——父话题那一轮一结束就会把它采信掉，
    而这些测试要的正是"卡还开着的时候发生了什么"。同 test_conclusion_cards。"""

    async def _run() -> dict:
        async with client.test_factory() as session:
            _, card = await TopicService(session).return_conclusion(
                subtopic_id=uuid.UUID(sub_id), conclusion=conclusion
            )
            assert card is not None
            out = {"id": str(card.id), "status": card.status}
            await session.commit()
            return out

    return asyncio.run(_run())


def _post_accept_card(client, place_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{place_id}/accept-card",
        json={
            "reviewer_handle": reviewer,
            "routing_reason": "最懂这块",
            # 递卡必须带提交标题 (#504)——它是这次改动留在 git 历史里的那一行。
            "change_subject": "fix(topic): deliver the batch this room worked on",
        },
    )


def _accept_cards(client, place_id: str) -> list[dict]:
    return client.get(f"/topics/{place_id}/accept-card").json()["data"]["data"]


def _conclusion_cards(client, place_id: str) -> list[dict]:
    return client.get(f"/topics/{place_id}/conclusion-cards").json()["data"]["data"]


def _topic_status(client, place_id: str) -> str:
    """A place's status. A thread says open/closed; a room says active/archived —
    two vocabularies because they are two different endings (work finished vs a
    person put the place away)."""
    return client.get(f"/topics/{place_id}").json()["data"]["status"]


def _settle_by_turn_end(client, parent_id: str) -> None:
    """机制①：父话题那一轮结束，还开着的结论卡默认采信。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await ConclusionCardService(session).settle_open_for_turn(
                receiver_topic_id=uuid.UUID(parent_id),
                turn_started_at=datetime.now(UTC) + timedelta(seconds=1),
            )
            await session.commit()

    asyncio.run(_run())


def _settle_by_timeout(client, sub_id: str) -> None:
    """机制①bis：30 分钟绝对超时——父话题那一轮根本没跑起来。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            live = await ConclusionCardRepository(session).live_for_task(
                uuid.UUID(sub_id)
            )
            assert live is not None
            live.digest_deadline_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()
        async with client.test_factory() as session:
            await ConclusionCardService(session).sweep_expired()
            await session.commit()

    asyncio.run(_run())


def _sweep_deferred(client, *, after_minutes: int = 0) -> list[str]:
    """跑一遍"把欠下的归档补上"的扫描，可以把时钟往前拨（宽限期）。"""

    async def _run() -> list[str]:
        async with client.test_factory() as session:
            ids = await ConclusionCardService(session).sweep_deferred_archives(
                now=datetime.now(UTC) + timedelta(minutes=after_minutes)
            )
            await session.commit()
            return [str(i) for i in ids]

    return asyncio.run(_run())


def _assert_closed_owing_nothing(client, sub_id: str) -> None:
    """采信的判据：支线收起了，结论采信了，而且没留下任何欠着的归档。"""
    assert _topic_status(client, sub_id) == "closed"
    card = _conclusion_cards(client, sub_id)[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settle_reason"] == "", "没欠归档就不该留标记"
    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == []


# --- 支线交活的出口就是 conclude，没有第二条 --------------------------------


def test_a_thread_cannot_file_a_card_so_settling_owes_no_archive(client):
    """这个文件的其余部分全都建在这一条上。

    支线递不了卡，所以采信的时候它身上不可能挂着一张等人的验收卡，也就没有
    「先欠着归档」这回事——那条路一次都不会走。做完的出口是 `cheese conclude`，
    错误信息必须把这句话说出来，否则读它的分身只知道被拒、不知道该干嘛。
    """
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "做一件事")
    _file_conclusion(client, sub["id"], "做完了")

    r = _post_accept_card(client, sub["id"])
    assert r.status_code == 422, r.text
    assert "cheese conclude" in r.json()["message"]
    assert _accept_cards(client, sub["id"]) == []

    _settle_by_turn_end(client, parent["id"])

    _assert_closed_owing_nothing(client, sub["id"])


# --- 三条采信的路，收起的行为必须一致 ----------------------------------------


def test_turn_end_settling_closes_the_thread(client):
    """机制①：父话题轮次结束的默认采信。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个数")
    _file_conclusion(client, sub["id"], "查到了：42")

    _settle_by_turn_end(client, parent["id"])

    _assert_closed_owing_nothing(client, sub["id"])


def test_the_timeout_sweep_closes_the_thread_too(client):
    """机制①bis：30 分钟绝对超时那条路——父话题那一轮根本没跑起来。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个数")
    _file_conclusion(client, sub["id"], "查到了：42")

    _settle_by_timeout(client, sub["id"])

    _assert_closed_owing_nothing(client, sub["id"])


def test_the_manual_accept_route_closes_the_thread_too(client):
    """人手动点采信，走的是同一条路。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个数")
    _file_conclusion(client, sub["id"], "查到了：42")
    conclusion_id = _conclusion_cards(client, sub["id"])[0]["id"]

    r = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{conclusion_id}/accept",
        json={"decided_by": "user-1"},
    )
    assert r.status_code == 200, r.text

    _assert_closed_owing_nothing(client, sub["id"])


# --- 边界：房间那张卡不是支线的事 --------------------------------------------


def test_the_rooms_pending_card_neither_holds_a_thread_open_nor_gets_eaten(client):
    """房间递的卡和支线的收起，两边互不相干——两个方向都会安静地错。

    往一边错：把房间那张卡当成「有人还在拍板」的理由，那么房间只要挂着一张卡，
    它派出去的每一件活就都收不起来了。往另一边错：收起支线时顺手收敛房间那张卡，
    那正是 2026-08-16 现场那次事故的形状——验收人从头到尾没机会点。
    """
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "我这件")
    _file_conclusion(client, sub["id"], "我这件做完了")
    r = _post_accept_card(client, parent["id"])
    assert r.status_code == 200, r.text
    card_id = r.json()["data"]["id"]

    _settle_by_turn_end(client, parent["id"])

    # 支线照常收起，没欠着。
    _assert_closed_owing_nothing(client, sub["id"])
    # 房间那张卡一个字没动，还等着人。
    (card,) = _accept_cards(client, parent["id"])
    assert card["id"] == card_id
    assert card["status"] == AcceptStatus.pending


def test_a_thread_in_an_archived_room_is_untouched(client):
    """房间已经归档：采信照常结算，支线照常是收起的，也不留任何待办。

    收起支线的路只有平台自己走（采信、或者房间被归档带走）——没有
    `POST /topics/{id}/archive` 那样的按钮，因为一件活不是人「收进抽屉」的东西，
    它是干完的。所以这里通过归档房间来造出「已经收起」的局面。
    """
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "早就收工了")
    _file_conclusion(client, sub["id"], "结论")
    assert client.post(f"/topics/{parent['id']}/archive", json={}).status_code == 200

    _settle_by_turn_end(client, parent["id"])

    assert _topic_status(client, sub["id"]) == "closed"
    card = _conclusion_cards(client, sub["id"])[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settle_reason"] == ""
