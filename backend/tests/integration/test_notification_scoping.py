"""A project mailbox belongs to one person.

Dogfooding an agent onto the platform surfaced the opposite: the project
notification endpoints treated ``target_handle`` as an optional filter, so a
caller who omitted it read everyone's mail — and ``read-all`` cleared everyone's
unread state. These tests pin the recipient to whoever is calling.
"""

from tests.integration.conftest import session_auth_headers


def _project(client, name: str = "Mailbox") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _notify(
    client,
    project_id: str,
    title: str,
    *,
    target: str | None = None,
    level: str = "light",
    kind: str = "change_alert",
) -> dict:
    r = client.post(
        f"/api/projects/{project_id}/notifications",
        json={
            "level": level,
            "kind": kind,
            "title": title,
            "target_handle": target,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _titles(response) -> list[str]:
    return [n["title"] for n in response.json()["data"]["data"]]


def _unread(client, project_id: str, handle: str) -> int:
    r = client.get(
        f"/api/projects/{project_id}/notifications/unread-count",
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["unread"]


def test_list_serves_the_caller_their_own_mail_and_broadcasts(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    _notify(client, pid, "给bob", target="bob")
    _notify(client, pid, "全体注意")

    r = client.get(
        f"/api/projects/{pid}/notifications", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    assert _titles(r) == ["全体注意", "给alice"]
    assert r.json()["data"]["total"] == 2


def test_reading_someone_elses_mailbox_is_refused(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    for path in (f"/api/projects/{pid}/notifications", f"/api/projects/{pid}/inbox"):
        r = client.get(
            path,
            params={"target_handle": "bob"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 403, (path, r.text)

    # ...and the caller's own handle is of course fine.
    r = client.get(
        f"/api/projects/{pid}/notifications",
        params={"target_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200


def test_read_all_leaves_other_peoples_notifications_unread(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    bobs = _notify(client, pid, "给bob", target="bob")

    r = client.post(
        f"/api/projects/{pid}/notifications/read-all",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["marked"] == 1

    assert _unread(client, pid, "alice") == 0
    assert _unread(client, pid, "bob") == 1
    still_unread = client.get(
        f"/api/projects/{pid}/notifications", headers=session_auth_headers("bob")
    )
    assert [n["read_at"] for n in still_unread.json()["data"]["data"]] == [None]
    assert still_unread.json()["data"]["data"][0]["id"] == bobs["id"]


def test_read_all_refuses_a_caller_who_names_nobody(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")

    r = client.post(f"/api/projects/{pid}/notifications/read-all")
    assert r.status_code == 401, r.text
    assert _unread(client, pid, "alice") == 1


def test_unread_count_and_inbox_default_to_the_caller(client):
    pid = _project(client)
    _notify(
        client,
        pid,
        "alice拍板",
        target="alice",
        level="strong",
        kind="decision_request",
    )
    _notify(
        client, pid, "bob拍板", target="bob", level="strong", kind="decision_request"
    )

    assert _unread(client, pid, "alice") == 1

    r = client.get(f"/api/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert _titles(r) == ["alice拍板"]


def test_an_unidentified_caller_sees_broadcasts_only(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    _notify(client, pid, "全体注意")

    r = client.get(f"/api/projects/{pid}/notifications")
    assert _titles(r) == ["全体注意"]
