"""驳回验收卡要叫醒芝士 (`POST /accept-cards/{id}/reject`).

`AcceptService.reject` only writes the row — no message, no summon. So a rejected
topic sat there until a human came back and poked it, while a CI failure on the
same card DOES summon (`_nudge_pr_fix`): same "去改代码" verdict, opposite
behaviour, and from the room the difference was invisible.

The reason has to travel too. "被退了" without "退在哪" leaves 芝士 guessing, and
the guess is usually "redo it".
"""

from tests.conftest import wait_work_idle
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    return client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    ).json()["data"]["id"]


def _card(client, topic_id: str, reviewer: str = "alice") -> str:
    return client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    ).json()["data"]["id"]


def _reject(client, card_id: str, reviewer: str = "alice", note: str = ""):
    return client.post(
        f"/api/accept-cards/{card_id}/reject",
        json={"decided_by": reviewer, "note": note},
        headers=session_auth_headers(reviewer),
    )


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]


def test_reject_wakes_the_topic_with_the_reason(client, stub_agent):
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    r = _reject(client, cid, note="迁移没加索引，列表页会全表扫")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "rejected"
    wait_work_idle()

    # 叫醒: a turn ran, and the reviewer's reason reached the agent verbatim.
    assert stub_agent.last_prompt is not None
    assert "迁移没加索引，列表页会全表扫" in stub_agent.last_prompt
    assert "alice" in stub_agent.last_prompt
    assert "【平台】" in stub_agent.last_prompt
    # 重递不被阻塞 —— saying so matters: the agent must not think it is stuck.
    assert "重新递卡" in stub_agent.last_prompt


def test_reject_leaves_a_room_visible_line_with_the_reason_in_meta(client):
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="口径和上一版对不上")
    wait_work_idle()

    events = [b for b in _blocks(client, tid) if b["kind"] == "event"]
    rejected = [
        e for e in events if (e.get("meta") or {}).get("event_type") == "card_rejected"
    ]
    assert len(rejected) == 1
    assert "驳回" in rejected[0]["content"]
    assert "alice" in rejected[0]["content"]
    meta = rejected[0]["meta"]
    assert meta["detail"] == "口径和上一版对不上"
    assert meta["detail_label"] == "驳回理由"
    assert meta["severity"] == "warn"


def test_reject_without_a_reason_still_wakes_and_says_there_is_none(client, stub_agent):
    """A reviewer who writes nothing is common. The turn must still happen, and
    must not invent a reason."""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="")
    wait_work_idle()

    assert "没写理由" in (stub_agent.last_prompt or "")


def test_rejected_topic_stays_active(client):
    """Unchanged behaviour, asserted because the wake-up would be pointless
    otherwise: an archived topic runs no turns."""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="改一下")
    wait_work_idle()

    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"
