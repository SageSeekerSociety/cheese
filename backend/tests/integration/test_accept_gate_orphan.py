"""历史 pending_gate 孤儿卡的出口 —— 扫底判死 / 人工作废.

采纳即合并 (docs/accept-is-merge.md #296, stage 1) 退役了机器闸门：新卡再也不会
born `pending_gate`。但退役之前留在库里的 `pending_gate` 行还得有出路——否则那些
话题永远递不出新卡（`create_card` 的互斥仍然认这个状态）。这两条出路都保留：

* **扫底判死**（`review/gate_sweep.py`）：闸门 runner 早就不再 dispatch，所以
  `gate.in_flight_card_ids()` 恒为空，扫底会把每一张过了判死线的历史 `pending_gate`
  卡判成 `gate_failed`，解开话题的死锁。
* **人工作废**（`AcceptService.void`）：给人的那个出口，同样覆盖历史 `pending_gate`
  以及仍然活着的 `conflict` / `pr_open`。

这些测试**直接在库里种一张 `pending_gate` 行**来复现历史现场（退役后 API 已经造不
出这个状态了），断言的全是外部可观察的行为：卡的状态、note、话题里的消息、以及最
关键的那条——扫底/作废之后同一个话题能不能重新递卡。
"""

import asyncio
import time
import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.review import gate_sweep
from tests.conftest import wait_work_idle
from tests.integration.conftest import room_text, session_auth_headers


@pytest.fixture(autouse=True)
def _authenticated_project_owner(client):
    client.headers.update(session_auth_headers("alice"))
    yield
    client.headers.pop("Authorization", None)


def _make_project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    return client.post(
        "/topics", json={"project_id": project_id, "title": "做一个东西"}
    ).json()["data"]["id"]


def _seed_pending_gate_card(client, topic_id: str, reviewer: str = "alice") -> str:
    """Insert a historical `pending_gate` row directly — the shape a card had
    before the gate was retired (the API can no longer mint this state)."""
    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _do() -> str:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(topic_id),
                reviewer_handle=reviewer,
                routing_reason="最懂",
                status=AcceptStatus.pending_gate,
            )
            session.add(card)
            await session.flush()
            cid = str(card.id)
            await session.commit()
            return cid

    return asyncio.run(_do())


def _orphan_card(client) -> tuple[str, str]:
    """一张真正的孤儿卡：库里一行 `pending_gate`，没有任何闸门任务在跑（退役后
    这本就是常态）。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _seed_pending_gate_card(client, tid)
    card = _latest_card(client, tid)
    assert card["id"] == cid
    assert card["status"] == "pending_gate"
    assert card["gate_started_at"] is None
    return tid, cid


def _latest_card(client, topic_id: str) -> dict:
    cards = client.get(f"/topics/{topic_id}/accept-card").json()["data"]["data"]
    assert cards
    return cards[0]


def _file_card(client, topic_id: str, reviewer: str = "alice"):
    return client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def _sweep(client) -> dict:
    r = client.post("/admin/scheduler/sweep-abandoned-gates")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _deadline_passed(monkeypatch) -> None:
    """把判死线拨到"现在"：任何已经存在的卡都算超时。动的是两个时长旋钮而不是
    替掉判定函数——测的还是真正那套 `COALESCE(gate_started_at, created_at) <
    cutoff` 的判据。"""
    monkeypatch.setattr(gate_sweep, "GATE_TIMEOUT_S", 0)
    monkeypatch.setattr(gate_sweep, "GATE_STALE_GRACE_S", 0)


def _blocks_text(client, topic_id: str) -> str:
    """房间里说了什么 —— 一行 content 加上折叠起来的 meta.detail。

    平台提示统一契约之后，「不是检查没通过、重新递一次卡」这段说明不再铺在房间的
    正文里，它在 `meta.detail`（前端折叠展示，芝士照样从 API 读全量）。所以断言
    「说没说这句话」必须把两半都算上 —— 见 `tests/integration/conftest.room_text`。
    """
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return room_text(blocks)


# --------------------------------------------------------------------------
# 扫底判死 —— 以及它解开的那个死锁
# --------------------------------------------------------------------------


def test_abandoned_gate_card_is_condemned_and_topic_can_file_again(client, monkeypatch):
    """核心验收标准：历史孤儿卡被判死后，同一话题能成功重递新卡。"""
    tid, card_id = _orphan_card(client)

    # 死锁存在：卡在 pending_gate 上，这个话题递不出第二张卡。
    blocked = _file_card(client, tid, "bob")
    assert blocked.status_code == 422

    _deadline_passed(monkeypatch)
    assert card_id in _sweep(client)["condemned"]

    condemned = _latest_card(client, tid)
    assert condemned["id"] == card_id
    assert condemned["status"] == "gate_failed"
    # 没有人做过这个决定，所以不能假装有人做过。
    assert condemned["decided_by"] is None

    # 死锁解开：重递成功，新卡（退役后）直接 born pending。
    monkeypatch.undo()  # 恢复被拨快的判死线
    fresh = _file_card(client, tid, "bob")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["data"]["status"] == "pending"


def test_condemned_card_says_the_gate_never_finished_not_that_it_failed(
    client, monkeypatch
):
    """「闸门没跑完」和「检查未通过」都落在 gate_failed 上，但对芝士意味着相反的
    下一步（重递 vs 去修代码）。所以卡面和给芝士的消息都必须把两者分开。"""
    tid, _ = _orphan_card(client)
    _deadline_passed(monkeypatch)
    _sweep(client)

    card = _latest_card(client, tid)
    assert "闸门没跑完" in card["note"]
    assert "闸门没跑完" in card["gate_output"]
    assert "检查未通过" not in card["note"] + card["gate_output"]

    # 芝士被叫醒去**重递**，而且被明说不是它的代码有问题。
    text = ""
    deadline = time.time() + 10
    while time.time() < deadline:
        wait_work_idle()
        text = _blocks_text(client, tid)
        if "闸门没跑完" in text:
            break
        time.sleep(0.05)
    assert "闸门没跑完" in text
    assert "重新递" in text


def test_sweep_is_idempotent(client, monkeypatch):
    tid, card_id = _orphan_card(client)
    _deadline_passed(monkeypatch)

    assert _sweep(client)["condemned"] == [card_id]
    # 第二轮什么也不做：判死后的卡是终态，查询不再选中它。
    assert _sweep(client)["condemned"] == []
    note = _latest_card(client, tid)["note"]
    assert note.count("闸门没跑完") == 1


def test_sweep_leaves_a_fresh_card_alone(client):
    """判死线是真的时长，不是"看见 pending_gate 就杀"。"""
    tid, card_id = _orphan_card(client)
    assert _sweep(client)["condemned"] == []
    assert _latest_card(client, tid)["status"] == "pending_gate"
    assert card_id  # 卡还在，还是那张


# --------------------------------------------------------------------------
# 人工作废 —— 给人的那个出口
# --------------------------------------------------------------------------


def _void(client, card_id: str, handle: str | None = None, note: str = "", **kw):
    headers = dict(kw.pop("headers", {}))
    if handle is not None:
        headers.update(session_auth_headers(handle))
    return client.post(
        f"/accept-cards/{card_id}/void", json={"note": note}, headers=headers, **kw
    )


def test_reviewer_can_void_a_stuck_card_and_the_topic_can_file_again(
    client, monkeypatch
):
    tid, card_id = _orphan_card(client)
    assert _file_card(client, tid, "bob").status_code == 422  # 死锁在

    r = _void(client, card_id, "alice", note="闸门丢了，重来")
    assert r.status_code == 200, r.text
    voided = r.json()["data"]
    assert voided["status"] == "revoked"
    assert "作废" in voided["note"]
    assert "闸门丢了，重来" in voided["note"]

    # 出口生效：能重递了。
    assert _file_card(client, tid, "bob").status_code == 200
    assert "作废" in _blocks_text(client, tid)


def test_void_puts_the_card_in_a_terminal_state_never_back_to_pending(client):
    """硬边界：作废不是"强行放行"。放行会让卡面的绿勾替一段没被检查过的代码
    背书；作废后必须重新递卡。"""
    tid, card_id = _orphan_card(client)
    _void(client, card_id, "alice")

    card = _latest_card(client, tid)
    assert card["status"] != "pending"
    assert card["gate_passed_at"] is None
    # 终态：不能被采纳，也不能被驳回。
    assert (
        client.post(
            f"/accept-cards/{card_id}/accept", json={"decided_by": "alice"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/accept-cards/{card_id}/reject", json={"decided_by": "alice"}
        ).status_code
        == 422
    )


def test_project_lead_can_void_but_an_ordinary_member_cannot(client):
    tid, card_id = _orphan_card(client)
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    for handle, role in (("lead-user", "lead"), ("member-user", "member")):
        r = client.post(
            f"/projects/{pid}/members", json={"user_handle": handle, "role": role}
        )
        assert r.status_code == 200

    assert _void(client, card_id, "member-user").status_code == 403
    assert _void(client, card_id, "mallory").status_code == 403
    assert _latest_card(client, tid)["status"] == "pending_gate"

    assert _void(client, card_id, "lead-user").status_code == 200
    assert _latest_card(client, tid)["status"] == "revoked"


def test_void_requires_a_logged_in_human(client):
    tid, card_id = _orphan_card(client)
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]

    # 匿名（全局 sandbox token 仍在，证明它不足以顶一个身份）。
    client.headers.pop("Authorization")
    assert _void(client, card_id).status_code == 401

    # 芝士拿着**作用域内**的 token 也不行。作废是授权类动作，只给人。
    r = _void(
        client, card_id, headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)}
    )
    assert r.status_code == 422, r.text
    assert "AI" in r.json()["message"]

    assert _latest_card(client, tid)["status"] == "pending_gate"


def test_void_rejects_a_card_that_is_already_settled(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    r = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )
    card = r.json()["data"]
    assert card["status"] == "pending"

    r = client.post(f"/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})
    assert r.status_code == 200

    r = _void(client, card["id"], "alice")
    assert r.status_code == 422
    # 已采纳的卡的既有语义没被动过：撤销仍然走 revoke。
    assert _latest_card(client, tid)["status"] == "accepted"


def test_void_is_scoped_to_one_card(client):
    """两个话题各卡一张，作废其中一张不影响另一张。"""
    tid_a, card_a = _orphan_card(client)
    tid_b, card_b = _orphan_card(client)
    assert uuid.UUID(card_a) != uuid.UUID(card_b)

    assert _void(client, card_a, "alice").status_code == 200
    assert _latest_card(client, tid_a)["status"] == "revoked"
    assert _latest_card(client, tid_b)["status"] == "pending_gate"
