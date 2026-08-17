"""Notification domain over HTTP — spec §8.5/8.6, evals G2/G3."""

import asyncio
import uuid

from app.domain.alert.models import AlertKind, AlertLevel
from app.domain.alert.repositories import AlertRepository
from tests.integration.conftest import session_auth_headers

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _create_project(client, name: str = "Demo") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _post_notif(client, project_id: str, **body) -> dict:
    r = client.post(f"/projects/{project_id}/alerts", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_notification_quota_per_topic(client):
    # spec §8.5: ≤2 light/day and ≤1 strong/week per topic; silent uncapped.
    pid = _create_project(client)
    tid = client.post("/topics", json={"project_id": pid, "title": "T"}).json()["data"][
        "id"
    ]
    p, t = uuid.UUID(pid), uuid.UUID(tid)

    async def run():
        async with client.test_factory() as s:
            repo = AlertRepository(s)

            async def add(level):
                await repo.add(
                    project_id=p,
                    level=level,
                    kind=AlertKind.heartbeat,
                    title="x",
                    topic_id=t,
                )
                await s.commit()

            assert await repo.over_quota(t, AlertLevel.light) is False
            await add(AlertLevel.light)
            await add(AlertLevel.light)
            assert await repo.over_quota(t, AlertLevel.light) is True

            assert await repo.over_quota(t, AlertLevel.strong) is False
            await add(AlertLevel.strong)
            assert await repo.over_quota(t, AlertLevel.strong) is True

            # silent and non-topic notifications are never throttled.
            assert await repo.over_quota(t, AlertLevel.silent) is False
            assert await repo.over_quota(None, AlertLevel.light) is False

    asyncio.run(run())


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
        f"/projects/{NIL_UUID}/alerts",
        json={"level": "silent", "kind": "heartbeat", "title": "x"},
    )
    assert r.status_code == 404


def test_create_notification_invalid_level_rejected(client):
    pid = _create_project(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json={"level": "loud", "kind": "heartbeat", "title": "x"},
    )
    # The merged app maps request-validation errors to 400 (知是 convention),
    # not FastAPI's default 422 (app/core/errors.validation_exception_handler).
    assert r.status_code == 400


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

    # A caller who names no recipient is not "everyone" — they get the
    # broadcasts and nothing addressed to a person.
    r = client.get(f"/projects/{pid}/alerts")
    body = r.json()["data"]
    assert body["total"] == 1
    assert [n["title"] for n in body["data"]] == ["第一条"]

    # A signed-in alice sees her own AND broadcasts (第一条 has no
    # target_handle), but not bob's (第二条). Newest first.
    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers("alice"))
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
    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers("alice"))
    titles = [n["title"] for n in r.json()["data"]["data"]]
    assert "全体注意" in titles  # broadcast reaches alice
    assert "给bob" not in titles  # bob's private one does not


def test_list_unread_only(client):
    pid = _create_project(client)
    a = _post_notif(client, pid, level="light", kind="change_alert", title="A")
    _post_notif(client, pid, level="light", kind="change_alert", title="B")

    # Mark one read (a broadcast: any signed-in caller may act on it).
    rr = client.post(f"/alerts/{a['id']}/read", headers=session_auth_headers("user-1"))
    assert rr.status_code == 200

    r = client.get(f"/projects/{pid}/alerts", params={"unread_only": "true"})
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

    # Kind is what decides the inbox, not who it is for: the alert and the
    # heartbeat are broadcasts, so they reach both recipients' lists — yet
    # neither inbox carries them.
    alice = client.get(
        f"/projects/{pid}/inbox", headers=session_auth_headers("alice")
    ).json()["data"]
    bob = client.get(
        f"/projects/{pid}/inbox", headers=session_auth_headers("bob")
    ).json()["data"]
    assert [n["title"] for n in alice["data"]] == ["拍板"]
    assert [n["title"] for n in bob["data"]] == ["验收"]
    assert {n["kind"] for n in alice["data"] + bob["data"]} == {
        "decision_request",
        "accept_request",
    }

    # A decision request stays in the inbox after merely being read — it leaves
    # only once 拍板 (resolved).
    client.post(
        f"/alerts/{decision['id']}/read",
        headers=session_auth_headers("alice"),
    )
    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert r.json()["data"]["total"] == 1

    client.post(
        f"/alerts/{decision['id']}/resolve",
        json={"chosen": "随便"},
        headers=session_auth_headers("alice"),
    )
    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert r.json()["data"]["total"] == 0


def test_resolve_records_choice_and_posts_block(client):
    pid = _create_project(client)
    topic = client.post(
        "/topics", json={"project_id": pid, "title": "切分方案"}
    ).json()["data"]["id"]
    # A decision request with options, scoped to a topic.
    n = _post_notif(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="评测集怎么切分",
        target_handle="user-1",
        topic_id=topic,
        payload={"options": ["按时间切分", "随机切分"]},
    )
    r = client.post(
        f"/alerts/{n['id']}/resolve",
        json={"chosen": "按时间切分"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["resolved_at"] is not None
    assert data["payload"]["resolved_choice"] == "按时间切分"
    # The decision is dropped into the topic so 芝士 sees it next turn, and it
    # is attributed to the verified caller, not to anything the body said.
    blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    decision_block = next(b for b in blocks if "按时间切分" in b["content"])
    assert decision_block["author"] == "user-1"
    # An option not in the list is rejected.
    n2 = _post_notif(
        client,
        pid,
        level="strong",
        kind="decision_request",
        title="再来一个",
        payload={"options": ["A", "B"]},
    )
    bad = client.post(
        f"/alerts/{n2['id']}/resolve",
        json={"chosen": "C"},
        headers=session_auth_headers("user-1"),
    )
    assert bad.status_code == 422


def test_mark_read_sets_timestamp(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")
    assert n["read_at"] is None

    r = client.post(f"/alerts/{n['id']}/read", headers=session_auth_headers("user-1"))
    assert r.status_code == 200
    assert r.json()["data"]["read_at"] is not None


def test_mark_read_missing_404(client):
    r = client.post(f"/alerts/{NIL_UUID}/read")
    assert r.status_code == 404


def test_feedback_up_and_down(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")
    headers = session_auth_headers("user-1")

    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "up"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "up"

    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "down"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["feedback"] == "down"


def test_feedback_invalid_422(client):
    pid = _create_project(client)
    n = _post_notif(client, pid, level="light", kind="change_alert", title="X")
    r = client.post(
        f"/alerts/{n['id']}/feedback",
        json={"feedback": "meh"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 422


def test_feedback_missing_404(client):
    r = client.post(f"/alerts/{NIL_UUID}/feedback", json={"feedback": "up"})
    assert r.status_code == 404
