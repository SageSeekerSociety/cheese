"""Notification domain over HTTP — spec §8.5/8.6, evals G2/G3."""

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _create_project(client, name: str = "Demo") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _post_notif(client, project_id: str, **body) -> dict:
    r = client.post(f"/api/projects/{project_id}/notifications", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_create_notification(client):
    pid = _create_project(client)
    data = _post_notif(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="文档更新了",
        body="改了三处",
        target_handle="alice",
        payload={"diff": 3},
    )
    assert data["level"] == "light"
    assert data["kind"] == "change_alert"
    assert data["title"] == "文档更新了"
    assert data["target_handle"] == "alice"
    assert data["payload"] == {"diff": 3}
    assert data["read_at"] is None
    assert data["feedback"] is None


def test_create_notification_missing_project_404(client):
    r = client.post(
        f"/api/projects/{NIL_UUID}/notifications",
        json={"level": "silent", "kind": "heartbeat", "title": "x"},
    )
    assert r.status_code == 404


def test_create_notification_invalid_level_422(client):
    pid = _create_project(client)
    r = client.post(
        f"/api/projects/{pid}/notifications",
        json={"level": "loud", "kind": "heartbeat", "title": "x"},
    )
    assert r.status_code == 422


def test_list_newest_first_and_filters(client):
    pid = _create_project(client)
    _post_notif(client, pid, level="silent", kind="heartbeat", title="第一条")
    _post_notif(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="第二条",
        target_handle="bob",
    )
    _post_notif(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="第三条",
        target_handle="alice",
    )

    r = client.get(f"/api/projects/{pid}/notifications")
    body = r.json()["data"]
    assert body["total"] == 3
    # Newest first.
    assert [n["title"] for n in body["data"]] == ["第三条", "第二条", "第一条"]

    # Filter by target_handle: alice sees her own AND broadcasts (第一条 has no
    # target_handle), but not bob's (第二条).
    r = client.get(
        f"/api/projects/{pid}/notifications", params={"target_handle": "alice"}
    )
    body = r.json()["data"]
    assert body["total"] == 2
    assert [n["title"] for n in body["data"]] == ["第三条", "第一条"]


def test_broadcast_visible_to_everyone(client):
    # A broadcast (no target_handle) reaches every user's list (spec §8.5).
    pid = _create_project(client)
    _post_notif(client, pid, level="strong", kind="change_alert", title="全体注意")
    _post_notif(
        client,
        pid,
        level="light",
        kind="change_alert",
        title="给bob",
        target_handle="bob",
    )
    r = client.get(
        f"/api/projects/{pid}/notifications", params={"target_handle": "alice"}
    )
    titles = [n["title"] for n in r.json()["data"]["data"]]
    assert "全体注意" in titles  # broadcast reaches alice
    assert "给bob" not in titles  # bob's private one does not


def test_list_unread_only(client):
    pid = _create_project(client)
    a = _post_notif(client, pid, level="light", kind="change_alert", title="A")
    _post_notif(client, pid, level="light", kind="change_alert", title="B")

    # Mark one read.
    rr = client.post(f"/api/notifications/{a['id']}/read")
    assert rr.status_code == 200

    r = client.get(f"/api/projects/{pid}/notifications", params={"unread_only": "true"})
    body = r.json()["data"]
    assert body["total"] == 1
    assert body["data"][0]["title"] == "B"


def test_inbox_only_unread_decision_and_accept(client):
    pid = _create_project(client)
    # Not in inbox: change_alert + heartbeat.
    _post_notif(client, pid, level="light", kind="change_alert", title="alert")
    _post_notif(client, pid, level="silent", kind="heartbeat", title="beat")
    # In inbox: decision_request + accept_request.
    decision = _post_notif(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="拍板",
        target_handle="alice",
    )
    _post_notif(
        client,
        pid,
        level="strong",
        kind="accept_request",
        title="验收",
        target_handle="bob",
    )

    r = client.get(f"/api/projects/{pid}/inbox")
    body = r.json()["data"]
    assert body["total"] == 2
    kinds = {n["kind"] for n in body["data"]}
    assert kinds == {"decision_request", "accept_request"}

    # Filter inbox by target_handle.
    r = client.get(f"/api/projects/{pid}/inbox", params={"target_handle": "alice"})
    body = r.json()["data"]
    assert body["total"] == 1
    assert body["data"][0]["title"] == "拍板"

    # Once read, it leaves the inbox.
    client.post(f"/api/notifications/{decision['id']}/read")
    r = client.get(f"/api/projects/{pid}/inbox", params={"target_handle": "alice"})
    assert r.json()["data"]["total"] == 0


def test_mark_read_sets_timestamp(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")
    assert n["read_at"] is None

    r = client.post(f"/api/notifications/{n['id']}/read")
    assert r.status_code == 200
    assert r.json()["data"]["read_at"] is not None


def test_mark_read_missing_404(client):
    r = client.post(f"/api/notifications/{NIL_UUID}/read")
    assert r.status_code == 404


def test_feedback_up_and_down(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")

    r = client.post(f"/api/notifications/{n['id']}/feedback", json={"feedback": "up"})
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "up"

    r = client.post(f"/api/notifications/{n['id']}/feedback", json={"feedback": "down"})
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "down"


def test_feedback_invalid_422(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")
    r = client.post(f"/api/notifications/{n['id']}/feedback", json={"feedback": "meh"})
    assert r.status_code == 422


def test_feedback_missing_404(client):
    r = client.post(f"/api/notifications/{NIL_UUID}/feedback", json={"feedback": "up"})
    assert r.status_code == 404
