"""Integration tests for the Accept-card / Review domain (spec §4.4, §6.3).

Exercises the 验收 state machine through the FastAPI TestClient on an in-memory
SQLite DB: create card -> accept (marks the topic delivered, does NOT archive
it), the AI-can't-accept-own rule, double-accept, reject, and revoke (clears the
delivery marker).

The decision endpoints (accept/reject/revoke/reassign/approve) require a real
authenticated actor (Authorization: Bearer <session token>) and, for
accept/reject, that the actor IS the card's routed reviewer — see
test_accept_authorization.py for the security-focused cases.
"""

import uuid

from tests.integration.conftest import session_auth_headers


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "pending"
    assert card["reviewer_handle"] == reviewer
    assert card["routing_reason"] == "最懂"
    return card["id"]


def test_create_card_404_for_missing_topic(client):
    r = client.post(
        f"/topics/{uuid.uuid4()}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
        },
    )
    assert r.status_code == 404


def test_list_cards_newest_first(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    first = _make_card(client, tid, "alice")
    # One pending card per topic (not a broadcast): reject the first before a
    # second can be filed.
    client.post(
        f"/accept-cards/{first}/reject",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    second = _make_card(client, tid, "bob")

    r = client.get(f"/topics/{tid}/accept-card")
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 2
    items = body["data"]["data"]
    assert len(items) == 2
    # Newest first.
    assert items[0]["id"] == second
    assert items[0]["reviewer_handle"] == "bob"


def test_accept_marks_the_topic_delivered_and_leaves_it_active(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["decided_by"] == "alice"
    assert card["decided_at"] is not None

    # 交付完成 ≠ 话题结束 (#442 decision 1): 打上交付标记，话题照样活着。
    r = client.get(f"/topics/{tid}")
    topic = r.json()["data"]
    assert topic["status"] == "active"
    assert topic["accepted_by"] == "alice"
    assert topic["accepted_at"] is not None

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "accepted"


def test_merge_exception_leaves_card_and_topic_retryable(client, monkeypatch):
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    def fail_merge(*_args, **_kwargs):
        raise RuntimeError("git object database unavailable")

    monkeypatch.setattr(ws, "merge_topic", fail_merge)

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 422
    assert "could not be merged" in r.json()["message"]
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert cards[0]["approvals"] == []
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_ai_cannot_accept_own_work_collaborative(client):
    # Default project ai_mode is collaborative. Route the card TO the AI so the
    # reviewer-identity check passes and `_forbid_ai` is what actually fires.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "cheese")

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "cheese"},
        headers=session_auth_headers("cheese"),
    )
    assert r.status_code == 422
    assert "AI 不能验收自己做的东西" in r.json()["message"]

    # Card untouched, topic still active.
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_double_accept_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    assert (
        client.post(
            f"/accept-cards/{cid}/accept",
            json={"decided_by": "alice"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_accept_404_for_missing_card(client):
    r = client.post(
        f"/accept-cards/{uuid.uuid4()}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 404


def test_reject_keeps_topic_active(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "bob")

    r = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "bob", "note": "数据不够"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "rejected"
    assert card["decided_by"] == "bob"
    assert card["note"] == "数据不够"

    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_reject_then_accept_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_revoke_clears_the_delivery_marker(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "revoked"

    # 撤回采纳把话题带回"还没交付过" (spec §6.3: accept is revocable)，
    # 于是又能递卡了；status 一直是 active，撤销不碰归档。
    topic = client.get(f"/topics/{tid}").json()["data"]
    assert topic["status"] == "active"
    assert topic["accepted_by"] is None
    assert topic["accepted_at"] is None


def test_revoke_non_accepted_card_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    # Pending card cannot be revoked.
    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_only_one_pending_card_per_topic(client):
    # 不是广播 (spec §4.4): a second pending card on the same topic is rejected.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid, "alice")
    r = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "bob",
            "routing_reason": "x",
        },
    )
    assert r.status_code == 422


def test_no_new_card_after_delivery(client):
    # 防空 PR: 分支已经在 main 上，再递一张开出来的 PR 没有新提交。话题不归档，
    # 挡住第二张卡的是那张 accepted 的卡本身 (#442 decision 1)。
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    r = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "bob",
            "routing_reason": "x",
        },
    )
    assert r.status_code == 422
    # 拒的是「分支上没有新东西」这个事实，不是「这个话题交付过了」这段历史：
    # 房间接着干活、有了新提交就该能再递一张（见 test_accept_does_not_archive）。
    assert "没有新提交" in r.json()["message"]
    # 话题没有被归档 —— 拒绝的只是这一张空卡。
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_revoke_requires_authority(client):
    # Only the accepter (or owner/lead) can revoke — not any handle.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "stranger"},
        headers=session_auth_headers("stranger"),
    )
    assert r.status_code == 422
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] is None


def test_revoke_404_for_missing_card(client):
    r = client.post(
        f"/accept-cards/{uuid.uuid4()}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 404


def _main_log(pid: str, fmt: str) -> str:
    import subprocess

    from app.domain.workspace import service as ws

    return subprocess.run(
        ["git", "log", "-1", f"--format={fmt}", "main"],
        cwd=ws.ensure_repo(uuid.UUID(pid)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_accept_squashes_the_delivery_with_the_cards_words(client):
    """拍板 #363 (2026-09-07): an unbound project's accept lands ONE squash
    commit whose subject/body are the card's and whose trailers are pr_text's —
    same shape as the GitHub lane's product — with 芝士 as the committer. And
    before anything merges, the card reads CLEAN: no checks exist to wait for,
    so nothing may dress the wait up as an unknown (#718 的词表)."""
    import subprocess

    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    machine_commits(uuid.UUID(pid), uuid.UUID(tid), {"a.txt": "one\n"})
    machine_commits(uuid.UUID(pid), uuid.UUID(tid), {"b.txt": "two\n"})

    r = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "feat: deliver a and b",
            "change_body": "Two files, one delivery.",
            "reviewer_handle": "alice",
        },
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["merge_state"]["state"] == "clean"
    assert card["merge_state"]["who"] == "human"

    before = subprocess.run(
        ["git", "rev-list", "--count", "main"],
        cwd=ws.ensure_repo(uuid.UUID(pid)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200

    after = subprocess.run(
        ["git", "rev-list", "--count", "main"],
        cwd=ws.ensure_repo(uuid.UUID(pid)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(after) == int(before) + 1  # two branch commits → one on main

    body = _main_log(pid, "%B")
    assert body.splitlines()[0] == "feat: deliver a and b"
    assert "Two files, one delivery." in body
    assert "Reviewed-by: alice" in body
    assert f"Cheese-Topic: {tid}" in body
    assert f"Cheese-Card: {card['id']}" in body
    parents = _main_log(pid, "%P").split()
    assert len(parents) == 1  # squashed, not a merge commit
    committer = _main_log(pid, "%cn %ce").strip()
    assert committer == "芝士 cheese@zhishi.local"


def test_conflict_card_reads_dirty(client):
    """The one signal an unbound project has: the last merge hit a conflict.
    The card then says dirty (芝士处理中), not clean."""
    import subprocess

    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    machine_commits(uuid.UUID(pid), uuid.UUID(tid), {"f.txt": "branch version\n"})
    repo = ws.ensure_repo(uuid.UUID(pid))
    (repo / "f.txt").write_text("base version\n", encoding="utf-8")
    for args in (
        ["add", "-A"],
        ["-c", "user.name=x", "-c", "user.email=x@y", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    cid = _make_card(client, tid)
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "conflict"

    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["merge_state"]["state"] == "dirty"
    assert card["merge_state"]["who"] == "human"
