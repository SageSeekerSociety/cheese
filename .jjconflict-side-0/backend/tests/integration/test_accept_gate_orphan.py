"""pending_gate 孤儿卡死锁 —— 扫底判死 / 开跑打点 / 人工作废 (2026-08-11).

线上症状：一张验收卡在 `pending_gate` 上卡了 2 小时 44 分（同期健康的卡 17–23 秒
就落定）。闸门 runner 是纯内存的 asyncio task，后端一重启它就没了，再没人调
`finish_gate`。而 `pending_gate` 没有任何出口——accept / reject / revoke /
reassign 全拒，`create_card` 又因为它拒绝建新卡，于是**整个话题**递不出卡。

这里断言的全是外部可观察的行为：卡的状态、卡上的时间戳、note / gate_output 的
措辞、话题里的消息、以及最关键的那条——**扫底之后同一个话题能不能重新递卡**。
"""

import time
import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.review import gate, gate_sweep
from tests.conftest import wait_turns_idle
from tests.integration.conftest import session_auth_headers


@pytest.fixture(autouse=True)
def _gate_logs(tmp_path, monkeypatch):
    """Same deterministic gate harness as test_accept_gate.py — the check never
    touches a real workspace, and `sleep 2` gives a genuinely in-flight gate."""
    monkeypatch.setattr(gate, "LOG_DIR", tmp_path / "gate-logs")
    worktree = tmp_path / "gate-worktree"
    worktree.mkdir()
    monkeypatch.setattr(gate.ws, "topic_worktree", lambda *_: worktree)
    monkeypatch.setattr(gate.ws, "merge_topic", lambda *_: {"merged": True})

    def deterministic_gate_runner(_cwd, command, *, timeout, log_path):
        del timeout
        if command.startswith("sleep 2"):
            time.sleep(2)
        output = f"{command}\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        return {"exit_code": 3 if "exit 3" in command else 0, "tail": output}

    monkeypatch.setattr(gate.ws, "run_check_command", deterministic_gate_runner)


@pytest.fixture(autouse=True)
def _authenticated_project_owner(client):
    client.headers.update(session_auth_headers("alice"))
    yield
    client.headers.pop("Authorization", None)


def _make_project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    return client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    ).json()["data"]["id"]


def _set_gate(client, project_id: str, command: str) -> None:
    r = client.put(
        f"/api/projects/{project_id}/quality-gate", json={"check_command": command}
    )
    assert r.status_code == 200


def _file_card(client, topic_id: str, reviewer: str = "alice"):
    return client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )


def _latest_card(client, topic_id: str) -> dict:
    cards = client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]
    assert cards
    return cards[0]


def _sweep(client) -> dict:
    r = client.post("/api/admin/scheduler/sweep-abandoned-gates")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _deadline_passed(monkeypatch) -> None:
    """把判死线拨到"现在"：任何已经存在的卡都算超时。

    动的是两个时长旋钮而不是替掉判定函数——测的还是真正那套 `COALESCE(
    gate_started_at, created_at) < cutoff` 的判据。
    """
    monkeypatch.setattr(gate_sweep, "GATE_TIMEOUT_S", 0)
    monkeypatch.setattr(gate_sweep, "GATE_STALE_GRACE_S", 0)


def _orphan_card(client, monkeypatch) -> tuple[str, str]:
    """一张真正的孤儿卡：卡在 `pending_gate`，而闸门任务并不存在。

    做法是让 dispatch 什么也不做——这正是后端重启后现场的样子（卡在库里，
    task 随进程没了），而不是伪造一个数据库状态。
    """
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo never-runs")
    monkeypatch.setattr(gate, "dispatch", lambda *a, **kw: None)
    card = _file_card(client, tid).json()["data"]
    assert card["status"] == "pending_gate"
    assert card["gate_started_at"] is None  # 闸门压根没跑起来
    return tid, card["id"]


def _blocks_text(client, topic_id: str) -> str:
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    return "\n".join(b.get("content") or "" for b in blocks)


# --------------------------------------------------------------------------
# 1. gate_started_at：闸门跑没跑过、跑了多久，卡上看得见
# --------------------------------------------------------------------------


def test_gate_start_is_timestamped_between_filing_and_passing(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo ok")

    filed = _file_card(client, tid).json()["data"]
    assert filed["gate_started_at"] is None

    deadline = time.time() + 20
    while time.time() < deadline:
        card = _latest_card(client, tid)
        if card["status"] != "pending_gate":
            break
        time.sleep(0.05)
    assert card["status"] == "pending"
    # 三个时刻现在都在卡上，顺序也对：建卡 ≤ 开跑 ≤ 通过。
    assert card["gate_started_at"] is not None
    assert card["created_at"] <= card["gate_started_at"] <= card["gate_passed_at"]


# --------------------------------------------------------------------------
# 2. 扫底判死 —— 以及它解开的那个死锁
# --------------------------------------------------------------------------


def test_abandoned_gate_card_is_condemned_and_topic_can_file_again(client, monkeypatch):
    """本话题的核心验收标准：孤儿卡被判死后，同一话题能成功重递新卡。"""
    tid, card_id = _orphan_card(client, monkeypatch)

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

    # 死锁解开：重递成功，而且新卡正常走闸门。
    monkeypatch.undo()  # 恢复真的 dispatch（以及被拨快的判死线）
    fresh = _file_card(client, tid, "bob")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["data"]["status"] == "pending_gate"


def test_condemned_card_says_the_gate_never_finished_not_that_it_failed(
    client, monkeypatch
):
    """「闸门没跑完」和「检查未通过」都落在 gate_failed 上，但对芝士意味着相反的
    下一步（重递 vs 去修代码）。所以卡面和给芝士的消息都必须把两者分开。"""
    tid, _ = _orphan_card(client, monkeypatch)
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
        wait_turns_idle()
        text = _blocks_text(client, tid)
        if "闸门没跑完" in text:
            break
        time.sleep(0.05)
    assert "闸门没跑完" in text
    assert "重新递" in text


def test_sweep_is_idempotent(client, monkeypatch):
    tid, card_id = _orphan_card(client, monkeypatch)
    _deadline_passed(monkeypatch)

    assert _sweep(client)["condemned"] == [card_id]
    # 第二轮什么也不做：判死后的卡是终态，查询不再选中它。
    assert _sweep(client)["condemned"] == []
    note = _latest_card(client, tid)["note"]
    assert note.count("闸门没跑完") == 1


def test_sweep_leaves_a_fresh_card_alone(client, monkeypatch):
    """判死线是真的时长，不是"看见 pending_gate 就杀"。"""
    tid, card_id = _orphan_card(client, monkeypatch)
    assert _sweep(client)["condemned"] == []
    assert _latest_card(client, tid)["status"] == "pending_gate"
    assert card_id  # 卡还在，还是那张


def test_sweep_does_not_kill_a_gate_that_is_still_running(client, monkeypatch):
    """误杀防线之二：闸门确实在本进程里跑着的卡，即便过了判死线也不碰。"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "sleep 2; echo ok")
    card = _file_card(client, tid).json()["data"]
    assert card["status"] == "pending_gate"

    _deadline_passed(monkeypatch)
    assert _sweep(client)["condemned"] == []

    # 它自己正常跑完，绿灯照常递到验收人手上。
    deadline = time.time() + 20
    while time.time() < deadline:
        settled = _latest_card(client, tid)
        if settled["status"] != "pending_gate":
            break
        time.sleep(0.05)
    assert settled["status"] == "pending"


# --------------------------------------------------------------------------
# 3. 人工作废 —— 给人的那个出口
# --------------------------------------------------------------------------


def _void(client, card_id: str, handle: str | None = None, note: str = "", **kw):
    headers = dict(kw.pop("headers", {}))
    if handle is not None:
        headers.update(session_auth_headers(handle))
    return client.post(
        f"/api/accept-cards/{card_id}/void", json={"note": note}, headers=headers, **kw
    )


def test_reviewer_can_void_a_stuck_card_and_the_topic_can_file_again(
    client, monkeypatch
):
    tid, card_id = _orphan_card(client, monkeypatch)
    assert _file_card(client, tid, "bob").status_code == 422  # 死锁在

    r = _void(client, card_id, "alice", note="闸门丢了，重来")
    assert r.status_code == 200, r.text
    voided = r.json()["data"]
    assert voided["status"] == "revoked"
    assert "作废" in voided["note"]
    assert "闸门丢了，重来" in voided["note"]

    # 出口生效：能重递了。
    monkeypatch.undo()
    assert _file_card(client, tid, "bob").status_code == 200
    assert "作废" in _blocks_text(client, tid)


def test_void_puts_the_card_in_a_terminal_state_never_back_to_pending(
    client, monkeypatch
):
    """硬边界：作废不是"强行放行"。放行会让卡面的绿勾替一段没被检查过的代码
    背书；作废后必须重新递卡、重新过闸门。"""
    tid, card_id = _orphan_card(client, monkeypatch)
    _void(client, card_id, "alice")

    card = _latest_card(client, tid)
    assert card["status"] != "pending"
    assert card["gate_passed_at"] is None
    # 终态：不能被采纳，也不能被驳回。
    assert (
        client.post(
            f"/api/accept-cards/{card_id}/accept", json={"decided_by": "alice"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/accept-cards/{card_id}/reject", json={"decided_by": "alice"}
        ).status_code
        == 422
    )


def test_project_lead_can_void_but_an_ordinary_member_cannot(client, monkeypatch):
    tid, card_id = _orphan_card(client, monkeypatch)
    pid = client.get(f"/api/topics/{tid}").json()["data"]["project_id"]
    for handle, role in (("lead-user", "lead"), ("member-user", "member")):
        r = client.post(
            f"/api/projects/{pid}/members", json={"user_handle": handle, "role": role}
        )
        assert r.status_code == 200

    assert _void(client, card_id, "member-user").status_code == 403
    assert _void(client, card_id, "mallory").status_code == 403
    assert _latest_card(client, tid)["status"] == "pending_gate"

    assert _void(client, card_id, "lead-user").status_code == 200
    assert _latest_card(client, tid)["status"] == "revoked"


def test_void_requires_a_logged_in_human(client, monkeypatch):
    tid, card_id = _orphan_card(client, monkeypatch)
    pid = client.get(f"/api/topics/{tid}").json()["data"]["project_id"]

    # 匿名（全局 sandbox token 仍在，证明它不足以顶一个身份）。
    client.headers.pop("Authorization")
    assert _void(client, card_id).status_code == 401

    # 芝士拿着**作用域内**的 token 也不行。作废是授权类动作，只给人。
    # 这条特意走 HTTP：这条路由故意不在 `_CHEESE_WRITE_PATHS` 里，而本仓已经
    # 实证过"没列进白名单的写路由压根不过那个中间件，症状是静默放行而不是
    # 401"——所以"没加白名单"本身拦不住谁，必须验真正拦住它的那道。
    r = _void(
        client, card_id, headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)}
    )
    assert r.status_code == 422, r.text
    assert "AI" in r.json()["message"]

    assert _latest_card(client, tid)["status"] == "pending_gate"


def test_void_rejects_a_card_that_is_already_settled(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _file_card(client, tid).json()["data"]
    assert card["status"] == "pending"

    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200

    r = _void(client, card["id"], "alice")
    assert r.status_code == 422
    # 已采纳的卡的既有语义没被动过：撤销仍然走 revoke。
    assert _latest_card(client, tid)["status"] == "accepted"


def test_void_is_scoped_to_one_card(client, monkeypatch):
    """两个话题各卡一张，作废其中一张不影响另一张。"""
    tid_a, card_a = _orphan_card(client, monkeypatch)
    tid_b, card_b = _orphan_card(client, monkeypatch)
    assert uuid.UUID(card_a) != uuid.UUID(card_b)

    assert _void(client, card_a, "alice").status_code == 200
    assert _latest_card(client, tid_a)["status"] == "revoked"
    assert _latest_card(client, tid_b)["status"] == "pending_gate"
