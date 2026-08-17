"""归档别吃验收卡：默认采信不能把一张还等着人拍板的验收卡作废。

现场（2026-08-16）：子话题递了验收卡（PR 已开、CI 基本绿），父话题那一轮结束时
结论卡按默认采信结算，两分钟后验收卡变成 `revoked`，note 写着「📦 话题归档，
验收卡随之关闭」，验收人从头到尾没机会点。平台在两头同时鼓励这个组合——
`parent_link.md` 说「做完一件值得回报的事就 conclude，不用等全部干完」，
`stage_working.md` 说做完递卡——却没有任何一处说它们会互相踩。

错的不是归档收卡那条策略（归档话题上的 `pr_open` 卡还在被轮询器拿着凭据推进，
必须收），错的是**采信在还有人要拍板的时候就动手归档**。所以这里测两件事：

1. 卡还在等人时，卡不作废、子话题不归档——三条默认采信的路都要走同一条保护；
2. 推迟不是取消：卡一有结果，欠下的归档就得落下来，不能留一堆永不归档的子话题。

全部走外部可观察的行为（卡的状态、话题的状态、卡面的 settle_reason），不看实现。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentService
from app.domain.conclusion.models import (
    ARCHIVE_DEFERRED,
    ARCHIVE_DEFERRED_DONE,
    ARCHIVE_REFILE_GRACE_MINUTES,
    ConclusionStatus,
)
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.conclusion.services import ConclusionCardService
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.scheduler.service import SchedulerService
from app.domain.topic.services import TopicService
from tests.conftest import wait_turns_idle as _wait_turns_idle
from tests.integration.conftest import session_auth_headers

PAST_GRACE = ARCHIVE_REFILE_GRACE_MINUTES + 1


# --- harness ---------------------------------------------------------------


def _project(client) -> dict:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]


def _topic(client, project_id: str, title: str = "大话题") -> dict:
    return client.post(
        "/api/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]


def _split(client, parent_id: str, title: str) -> dict:
    sub = client.post(f"/api/topics/{parent_id}/split", json={"title": title}).json()[
        "data"
    ]
    # The 分身's auto-kickoff turn must finish before the test writes more.
    _wait_turns_idle()
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


def _file_accept_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂这块"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _accept_cards(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]


def _conclusion_cards(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/conclusion-cards").json()["data"]["data"]


def _topic_status(client, topic_id: str) -> str:
    return client.get(f"/api/topics/{topic_id}").json()["data"]["status"]


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
            live = await ConclusionCardRepository(session).live_for_topic(
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


def _force_card_status(client, card_id: str, status: AcceptStatus) -> None:
    """把一张卡直接摆到某个状态——`pr_open` 在测试里没法用真 GitHub 走出来。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.status = status
            await session.commit()

    asyncio.run(_run())


def _backdate_decision(client, card_id: str, *, minutes: int) -> None:
    """把"卡是什么时候被决议的"往前拨——重新递卡的宽限是从那一刻起算的。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.decided_at = datetime.now(UTC) - timedelta(minutes=minutes)
            await session.commit()

    asyncio.run(_run())


def _concluded_with_a_card_waiting(client) -> tuple[dict, dict, str, str]:
    """现场的标准形：子话题回流了结论 + 递了一张还没人决议的验收卡。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "做一件事")
    _file_conclusion(client, sub["id"], "做完了，PR 已开")
    card_id = _file_accept_card(client, sub["id"])
    return p, parent, sub["id"], card_id


def _assert_card_survived(client, sub_id: str, card_id: str) -> None:
    """本次修复的判据：卡还在等人，话题还活着，结论照样采信了。"""
    accept_card = _accept_cards(client, sub_id)[0]
    assert accept_card["id"] == card_id
    assert accept_card["status"] == AcceptStatus.pending, "验收卡被采信连带作废了"
    assert _topic_status(client, sub_id) == "active", "子话题被连带归档了"

    conclusion = _conclusion_cards(client, sub_id)[0]
    assert conclusion["status"] == ConclusionStatus.accepted, "结论没照常采信"
    assert conclusion["settle_reason"] == ARCHIVE_DEFERRED


# --- 1/2/3: 三条默认采信的路都要走同一条保护 --------------------------------


def test_turn_end_accept_leaves_the_pending_card_alone(client):
    """机制①：父话题轮次结束的默认采信——现场踩的就是这一条。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)

    _settle_by_turn_end(client, parent["id"])

    _assert_card_survived(client, sub_id, card_id)


def test_timeout_sweep_leaves_the_pending_card_alone(client):
    """机制①bis：30 分钟超时那条路同样不能吃卡。"""
    _, _parent, sub_id, card_id = _concluded_with_a_card_waiting(client)

    _settle_by_timeout(client, sub_id)

    _assert_card_survived(client, sub_id, card_id)


def test_manual_accept_route_leaves_the_pending_card_alone(client):
    """人手动点采信，走的是同一条保护。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    conclusion_id = _conclusion_cards(client, sub_id)[0]["id"]

    r = client.post(
        f"/api/topics/{parent['id']}/conclusion-cards/{conclusion_id}/accept",
        json={"decided_by": "user-1"},
    )
    assert r.status_code == 200

    _assert_card_survived(client, sub_id, card_id)


def test_the_subtopic_says_why_it_is_still_alive(client):
    """留痕：话题里必须有一句话解释它为什么没归档，否则只是换了种静默。"""
    _, parent, sub_id, _card_id = _concluded_with_a_card_waiting(client)

    _settle_by_turn_end(client, parent["id"])

    blocks = client.get(f"/api/topics/{sub_id}/blocks").json()["data"]["data"]
    assert any("暂不归档" in b["content"] for b in blocks)


# --- 7: pr_open 的卡（PR 开着等 CI）同样受保护 -------------------------------


def test_a_pr_open_card_is_protected_too(client):
    """现场那张卡就是 `pr_open`：PR 已开、CI 在跑。它比 `pending` 更不能被作废——
    卡一 revoke，平台就不再推进那个 PR 了。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _force_card_status(client, card_id, AcceptStatus.pr_open)

    _settle_by_turn_end(client, parent["id"])

    assert _accept_cards(client, sub_id)[0]["status"] == AcceptStatus.pr_open
    assert _topic_status(client, sub_id) == "active"


# --- 归档级联：孙子话题上等人的卡也算 ----------------------------------------


def test_a_grandchilds_pending_card_protects_the_subtopic(client):
    """归档是级联的：归档子话题会把孙子话题的卡一起收掉，所以孙子那张等人的卡
    同样构成"先别归档"的理由。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "中间那层")
    grandchild = _split(client, sub["id"], "再拆一层")
    _file_conclusion(client, sub["id"], "中间那层做完了")
    grandchild_card = _file_accept_card(client, grandchild["id"])

    _settle_by_turn_end(client, parent["id"])

    assert _accept_cards(client, grandchild["id"])[0]["id"] == grandchild_card
    assert _accept_cards(client, grandchild["id"])[0]["status"] == AcceptStatus.pending
    assert _topic_status(client, sub["id"]) == "active"
    assert _topic_status(client, grandchild["id"]) == "active"


# --- 5/6: 没有卡 / 已经归档 —— 行为一个字都不变 ------------------------------


def test_a_subtopic_without_an_accept_card_is_archived_as_before(client):
    """没有验收卡的子话题：采信即归档，跟以前完全一样。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个数")
    _file_conclusion(client, sub["id"], "查到了：42")

    _settle_by_turn_end(client, parent["id"])

    assert _topic_status(client, sub["id"]) == "archived"
    card = _conclusion_cards(client, sub["id"])[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settle_reason"] == "", "没欠归档就不该留标记"
    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == []


def test_an_already_archived_subtopic_is_untouched(client):
    """已经归档的子话题：采信照常结算，归档状态不变，也不留任何待办。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "早就收工了")
    _file_conclusion(client, sub["id"], "结论")
    assert client.post(f"/api/topics/{sub['id']}/archive", json={}).status_code == 200

    _settle_by_turn_end(client, parent["id"])

    assert _topic_status(client, sub["id"]) == "archived"
    card = _conclusion_cards(client, sub["id"])[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settle_reason"] == ""


# --- 4: 推迟不是取消 —— 卡一有结果，归档就得落下来 ---------------------------


def test_a_rejected_card_lets_the_deferred_archive_land(client):
    """驳回之后没人再递卡：宽限一过，欠下的归档落下来。这就是"不留僵尸"。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])

    r = client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": "alice", "note": "再改改"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == [sub_id]
    assert _topic_status(client, sub_id) == "archived"
    assert _conclusion_cards(client, sub_id)[0]["settle_reason"] == (
        ARCHIVE_DEFERRED_DONE
    )


def test_a_voided_card_lets_the_deferred_archive_land(client):
    """人工作废那条出口同理——`pr_open` 的卡只能这么收尾。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])
    _force_card_status(client, card_id, AcceptStatus.pr_open)

    r = client.post(
        f"/api/accept-cards/{card_id}/void",
        json={"note": "PR 关了"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == [sub_id]
    assert _topic_status(client, sub_id) == "archived"


def test_the_refile_window_is_respected(client):
    """驳回的意思是"回去改了再来"，而归档话题递不出卡。所以归档不能在决议的
    同一刻落下——先给一个重新递卡的窗口。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])
    client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": "alice", "note": "再改改"},
        headers=session_auth_headers("alice"),
    )

    assert _sweep_deferred(client) == [], "宽限还没过就归档了"
    assert _topic_status(client, sub_id) == "active"


def test_refiling_a_card_re_protects_the_subtopic(client):
    """改完重新递卡：又是一张等人的卡，保护重新生效，宽限过了也不归档。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])
    client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": "alice", "note": "再改改"},
        headers=session_auth_headers("alice"),
    )
    second = _file_accept_card(client, sub_id, "bob")

    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == []
    assert _topic_status(client, sub_id) == "active"
    assert _accept_cards(client, sub_id)[0]["id"] == second
    assert _accept_cards(client, sub_id)[0]["status"] == AcceptStatus.pending


def test_accepting_the_card_archives_the_subtopic_and_clears_the_deferral(client):
    """正路：验收人点了采纳——采纳即归档，话题该归档的照样归档，待办随之消解。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])

    r = client.post(
        f"/api/accept-cards/{card_id}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert _accept_cards(client, sub_id)[0]["status"] == AcceptStatus.accepted
    assert _topic_status(client, sub_id) == "archived"
    # 扫描不会再归档一次（已经是归档了），只把待办标记收掉。
    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == []
    assert _conclusion_cards(client, sub_id)[0]["settle_reason"] == (
        ARCHIVE_DEFERRED_DONE
    )


def test_the_platform_sweep_is_actually_wired_to_pay_it_back(client, tmp_path):
    """补账挂在平台每 60 秒那趟结论卡扫描上——没有这根线，"推迟"就真的成了
    "取消"。所以这条测试走的是调度器的入口，不是服务方法。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])
    client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": "alice", "note": "再改改"},
        headers=session_auth_headers("alice"),
    )
    _backdate_decision(client, card_id, minutes=PAST_GRACE)

    async def _tick() -> dict:
        scheduler = SchedulerService(
            chat_service=ChatService(
                session_factory=client.test_factory,
                agent=AgentService(model="stub"),
                base_system_prompt="你是芝士。",
                workspace_root=str(tmp_path / "ws"),
            )
        )
        return await scheduler.sweep_conclusion_cards()

    result = asyncio.run(_tick())
    assert result["archived"] == 1, result
    assert result["errors"] == []
    assert _topic_status(client, sub_id) == "archived"


def test_the_deferred_archive_only_ever_fires_once(client):
    """待办是一次性的：人后来手动取消归档，平台不能拿一条早就结算完的结论
    再把它关一次。"""
    _, parent, sub_id, card_id = _concluded_with_a_card_waiting(client)
    _settle_by_turn_end(client, parent["id"])
    client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": "alice", "note": "再改改"},
        headers=session_auth_headers("alice"),
    )
    assert _sweep_deferred(client, after_minutes=PAST_GRACE) == [sub_id]

    assert client.post(f"/api/topics/{sub_id}/unarchive", json={}).status_code == 200
    assert _sweep_deferred(client, after_minutes=10 * PAST_GRACE) == []
    assert _topic_status(client, sub_id) == "active"
