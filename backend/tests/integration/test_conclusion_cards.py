"""结论卡·阶段一 (docs/topics/结论卡阶段一.md).

The behaviour under test is 默认采信: a sub-topic's conclusion gets a card, and
the card settles itself unless somebody spends a turn saying otherwise. So most
of these tests assert what happens when NOBODY does anything.

Timing note: filing a card over HTTP (`POST /return-conclusion`) also wakes the
parent, and that digest turn ending is exactly what auto-accepts the card — so a
test that needs a card to STAY open files it through the service instead of the
route. That is not a shortcut around the API: `return_conclusion` is the same
call the route makes, minus the wake-up the test doesn't want.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.conclusion.models import ConclusionStatus
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.conclusion.services import ConclusionCardService
from app.domain.topic.services import TopicService
from tests.conftest import wait_work_idle as _wait_work_idle


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


def _cards(client, topic_id: str) -> list[dict]:
    """Cards PRODUCED by this topic, newest first."""
    return client.get(f"/topics/{topic_id}/conclusion-cards").json()["data"]["data"]


def _file_card(client, sub_id: str, conclusion: str) -> dict:
    """File a card the way the route does, but WITHOUT waking the parent — so the
    card stays open for the test to act on."""

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


def _topic_status(client, topic_id: str) -> str:
    """The place's status. A room says active/archived; a THREAD says open/closed.

    Two vocabularies on purpose: a room is archived by a person and freezes; a
    thread is collapsed when its work is done and can still be talked in. Using
    one word for both would hide exactly that difference.
    """
    return client.get(f"/topics/{topic_id}").json()["data"]["status"]


# --- 开卡 (纯加法) ---------------------------------------------------------


def test_conclude_files_a_card_without_touching_the_three_old_side_effects(client):
    """阶段一是纯加法：回流照旧插消息 / 织进父实况文档 / 发 change_alert，
    卡是第四件事。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "实现数据清洗")

    r = client.post(
        f"/topics/{sub['id']}/return-conclusion",
        json={"conclusion": "数据清洗完成，去重后剩 8000 条"},
    )
    assert r.status_code == 200
    _wait_work_idle()

    # 1) the card (new)
    cards = _cards(client, sub["id"])
    assert len(cards) == 1
    assert "数据清洗完成" in cards[0]["conclusion"]
    # 两端都是房间，出方由支线键说明：一件活在房间里结论给房间，不再是
    # 一个话题结论给另一个话题。
    assert cards[0]["receiver_topic_id"] == parent["id"], "收方必须是房间"
    assert cards[0]["topic_id"] == parent["id"]
    assert cards[0]["task_id"] == sub["id"]

    # 2) the three old side effects, unchanged
    blocks = client.get(f"/topics/{parent['id']}/blocks").json()["data"]["data"]
    assert any("数据清洗完成" in b["content"] for b in blocks), "父话题消息没了"
    doc = client.get(f"/topics/{parent['id']}/doc").json()["data"]
    assert doc is not None and "数据清洗完成" in doc["content"], "父实况文档没织进去"
    notifs = client.get(f"/projects/{p['id']}/alerts").json()["data"]["data"]
    assert any("实现数据清洗" in n["title"] for n in notifs), "change_alert 没发"


def test_conclude_to_an_archived_parent_files_no_card(client):
    """归档的父话题没有下一轮，卡永远没人结算——那就别开。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    client.post(f"/topics/{parent['id']}/archive", json={})

    r = client.post(
        f"/topics/{sub['id']}/return-conclusion", json={"conclusion": "做完了"}
    )
    assert r.status_code == 200
    _wait_work_idle()
    assert _cards(client, sub["id"]) == []


# --- 机制①: 轮结束自动采信 --------------------------------------------------


def test_turn_end_auto_accepts_the_card_and_archives_the_subtopic(client):
    """默认采信的主路径：父话题消化完那一轮结束，卡自动 accepted、子话题归档。
    没有任何人点过任何东西。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "查一个数")

    client.post(
        f"/topics/{sub['id']}/return-conclusion", json={"conclusion": "查到了：42"}
    )
    _wait_work_idle()

    card = _cards(client, sub["id"])[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settled_by"] == "system", "自动采信要记在平台头上，不是某个人"
    assert card["settled_at"] is not None
    assert _topic_status(client, sub["id"]) == "closed", "采信即收起"


def test_a_card_born_mid_turn_survives_that_turn(client):
    """轮结束只吞「这一轮开始前就存在」的卡：本轮跑到一半才产生的卡根本没被这一轮
    看见，它得活到真正消化它的那一轮。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论一")

    async def _settle_with_a_turn_that_started_before_the_card() -> list[str]:
        async with client.test_factory() as session:
            cards = await ConclusionCardService(session).settle_open_for_turn(
                receiver_topic_id=uuid.UUID(parent["id"]),
                turn_started_at=datetime.now(UTC) - timedelta(hours=1),
            )
            await session.commit()
            return [str(c.id) for c in cards]

    assert asyncio.run(_settle_with_a_turn_that_started_before_the_card()) == []
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open
    assert card["id"] == _cards(client, sub["id"])[0]["id"]


# --- 机制①bis: 30 分钟绝对超时 ----------------------------------------------


def test_expired_card_is_swept_and_accepted_even_if_no_turn_ever_ran(client):
    """父话题那一轮可能根本没跑起来（排队/崩溃/没额度）。卡不能永远挂着。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    _file_card(client, sub["id"], "结论")

    async def _expire_then_sweep() -> list[uuid.UUID]:
        async with client.test_factory() as session:
            live = await ConclusionCardRepository(session).live_for_topic(
                uuid.UUID(sub["id"])
            )
            assert live is not None
            live.digest_deadline_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()
        async with client.test_factory() as session:
            settled = await ConclusionCardService(session).sweep_expired()
            await session.commit()
            return settled

    assert len(asyncio.run(_expire_then_sweep())) == 1
    card = _cards(client, sub["id"])[0]
    assert card["status"] == ConclusionStatus.accepted
    assert card["settled_by"] == "system"
    assert _topic_status(client, sub["id"]) == "closed"


def test_sweep_survives_a_parent_and_child_card_expiring_together(client):
    """采信上级的卡会顺手结算下级的卡（归档级联）。如果同一批过期卡里两张都在，
    扫描器走到下级那张时它已经不是 open 了——不能因此炸掉整个 sweep，否则事务
    回滚、这批卡永远扫不掉，30 分钟兜底就成了摆设。"""
    p = _project(client)
    grandparent = _topic(client, p["id"], "祖")
    parent = _split(client, grandparent["id"], "父")
    sub = _split(client, parent["id"], "子")
    _file_card(client, sub["id"], "孙子的结论")  # 收方是「父」
    _file_card(client, parent["id"], "儿子的结论")  # 收方是「祖」

    async def _expire_parent_first_then_sweep() -> list[uuid.UUID]:
        now = datetime.now(UTC)
        async with client.test_factory() as session:
            repo = ConclusionCardRepository(session)
            # 让「父」那张先被处理：它一采信就会把「子」那张一并结算。
            upper = await repo.live_for_topic(uuid.UUID(parent["id"]))
            lower = await repo.live_for_topic(uuid.UUID(sub["id"]))
            assert upper is not None and lower is not None
            upper.digest_deadline_at = now - timedelta(minutes=10)
            lower.digest_deadline_at = now - timedelta(minutes=5)
            await session.commit()
        async with client.test_factory() as session:
            settled = await ConclusionCardService(session).sweep_expired()
            await session.commit()
            return settled

    asyncio.run(_expire_parent_first_then_sweep())

    assert _cards(client, parent["id"])[0]["status"] == ConclusionStatus.accepted
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.accepted
    assert _topic_status(client, parent["id"]) == "archived"
    assert _topic_status(client, sub["id"]) == "closed"


def test_sweep_leaves_a_card_that_still_has_time(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    _file_card(client, sub["id"], "结论")

    async def _sweep() -> list[uuid.UUID]:
        async with client.test_factory() as session:
            settled = await ConclusionCardService(session).sweep_expired()
            await session.commit()
            return settled

    assert asyncio.run(_sweep()) == []
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open


# --- 二次 conclude: superseded ----------------------------------------------


def test_second_conclude_supersedes_the_unsettled_card(client):
    """结算前又回流一次：老卡作废，新卡接手。这就是回流的幂等。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")

    first = _file_card(client, sub["id"], "结论一")
    second = _file_card(client, sub["id"], "结论二（改了）")
    assert second["id"] != first["id"]

    by_id = {c["id"]: c for c in _cards(client, sub["id"])}
    assert by_id[first["id"]]["status"] == ConclusionStatus.superseded
    assert by_id[second["id"]]["status"] == ConclusionStatus.open
    assert "结论二" in by_id[second["id"]]["conclusion"]
    # 作废是记账，不是判决：子话题不该因此被归档。
    assert _topic_status(client, sub["id"]) != "closed"


# --- 采信 / 补证据 / 升级 三条出口 -------------------------------------------


def test_accept_route_settles_the_card_and_archives_the_subtopic(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论")

    r = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/accept",
        json={"decided_by": "user-1"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == ConclusionStatus.accepted
    assert r.json()["data"]["settled_by"] == "user-1"
    assert _topic_status(client, sub["id"]) == "closed"

    # 结算过的卡不能再结算一次。
    again = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/accept", json={}
    )
    assert again.status_code == 422


def test_need_evidence_sends_the_card_back_and_wakes_the_subtopic(client):
    """补证据要付一整轮（唤醒子话题）——这个不对称正是「采信是默认」的原因。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论：这样最快")

    before = len(client.get(f"/topics/{sub['id']}/blocks").json()["data"]["data"])
    r = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/need-evidence",
        json={"decided_by": "user-1", "reason": "把基准测试的数跑出来"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == ConclusionStatus.returned
    assert r.json()["data"]["returned_count"] == 1
    _wait_work_idle()

    assert _topic_status(client, sub["id"]) != "closed", "打回不收起"
    blocks = client.get(f"/topics/{sub['id']}/blocks").json()["data"]["data"]
    assert len(blocks) > before, "子话题没被叫醒"
    # 一行给房间，要补的那句话在展开区里——两处都算送到了。
    said = [
        b["content"] + str((b.get("meta") or {}).get("detail") or "") for b in blocks
    ]
    assert any("基准测试" in t for t in said), "要补什么没传到子话题"


def test_need_evidence_requires_a_reason(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论")

    r = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/need-evidence",
        json={"reason": "   "},
    )
    assert r.status_code == 422
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open


def test_need_evidence_is_capped_so_a_card_cannot_ping_pong(client):
    """每张卡最多打回一次；用完只剩采信或升级。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论一")

    first = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/need-evidence",
        json={"reason": "补第一条"},
    )
    assert first.status_code == 200
    _wait_work_idle()

    # 子话题补完再回流：同一张卡回到 open，但打回次数留着。
    reopened = _file_card(client, sub["id"], "结论一（补了证据）")
    assert reopened["id"] == card["id"], "returned 的卡应当被重新打开，而不是另开一张"
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open

    second = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/need-evidence",
        json={"reason": "再补一条"},
    )
    assert second.status_code == 422
    # 上限咬住之后，采信这条路还通。
    accept = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/accept", json={}
    )
    assert accept.status_code == 200


def test_escalate_asks_a_human_and_keeps_the_subtopic_alive(client):
    """升级 = 这事得以某人的名义做出去。后续（人向卡/决策请求）还要有地方挂，
    所以子话题不归档。"""
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "建议直接上线")

    r = client.post(
        f"/topics/{parent['id']}/conclusion-cards/{card['id']}/escalate",
        json={"decided_by": "user-1", "reason": "上线要产品负责人拍板"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == ConclusionStatus.escalated
    assert _topic_status(client, sub["id"]) != "closed"

    notifs = client.get(f"/projects/{p['id']}/alerts").json()["data"]["data"]
    assert any(n["kind"] == "decision_request" for n in notifs), "没人被叫来拍板"


def test_escalate_requires_a_reason(client):
    p = _project(client)
    parent = _topic(client, p["id"])
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论")
    url = f"/topics/{parent['id']}/conclusion-cards/{card['id']}/escalate"

    # 空串被 schema 拦下 (400)，只有空白的被服务层拦下 (422) —— 两层都得拦，
    # 不然「要谁拍什么板」是空的，通知发出去也没人知道要干嘛。
    assert client.post(url, json={"reason": ""}).status_code == 400
    assert client.post(url, json={"reason": "   "}).status_code == 422
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open


# --- 作用域: 路由挂在父话题下 -------------------------------------------------


def test_a_card_addressed_to_another_parent_is_not_settleable_here(client):
    """路由挂在收方话题下是为了 per-turn token 的作用域校验对得上；卡面和 URL
    不一致时必须 404，否则任何话题都能替别人结算。"""
    p = _project(client)
    parent = _topic(client, p["id"], "父一")
    other = _topic(client, p["id"], "父二")
    sub = _split(client, parent["id"], "子活")
    card = _file_card(client, sub["id"], "结论")

    r = client.post(
        f"/topics/{other['id']}/conclusion-cards/{card['id']}/accept", json={}
    )
    assert r.status_code == 404
    assert _cards(client, sub["id"])[0]["status"] == ConclusionStatus.open


# --- 归档级联: 没有了 ---------------------------------------------------------
#
# 这里原本有两个用例，钉的是「先结算下级卡再归档」：孙子话题的结论卡收方是儿子，
# 直接归档儿子会把那张卡连人带卡冻住（归档后实况文档定格，再没人能结算）。
#
# 它们跟着它们防的那个场面一起删掉了。工作不嵌套——一条支线底下不会再挂一条支线
# ——所以「归档一个还挂着未结算下级结论卡的话题」这件事在结构上不再可能发生。
# 造一棵它拒绝再生成的树来验它，测的是一个已经不存在的机制。
