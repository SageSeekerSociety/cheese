"""@all / @here notify the whole topic roster (群播, fusion-design §3)."""

from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _project_topic(client, created_by: str = "alice") -> tuple[str, str]:
    """A room in a project whose team has bob and carol on it — rooms seat only
    people who are in the project."""
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    for handle in ("bob", "carol"):
        join_project_team(client, p["id"], handle)
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers(created_by),
    ).json()["data"]
    return p["id"], t["id"]


def _add(client, tid: str, handle: str, actor: str = "alice") -> None:
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": handle, "role": "member", "actor": actor},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200


def _post(client, tid: str, content: str) -> None:
    # A human-only post: the @all notifications fire as the human block is
    # stored, before any agent turn. The sender is the request's token, not a
    # body field.
    post_message(client, tid, "alice", {"content": content})


def _notifs(client, pid: str, handle: str) -> list[dict]:
    return client.get(
        f"/projects/{pid}/alerts",
        headers=session_auth_headers(handle),
    ).json()["data"]["data"]


def test_at_all_notifies_every_member_except_sender_and_cheese(client):
    pid, tid = _project_topic(client, created_by="alice")
    _add(client, tid, "bob")
    _add(client, tid, "carol")
    _post(client, tid, "<@all> 大家看一下")

    assert len(_notifs(client, pid, "bob")) == 1
    assert len(_notifs(client, pid, "carol")) == 1
    # sender is not notified of their own broadcast
    assert _notifs(client, pid, "alice") == []
    # 芝士 is a member but the agent is summoned separately, never @-notified
    assert _notifs(client, pid, "cheese") == []


def test_at_here_equals_all_for_now(client):
    pid, tid = _project_topic(client, created_by="alice")
    _add(client, tid, "bob")
    _post(client, tid, "<@here> 在的人")
    assert len(_notifs(client, pid, "bob")) == 1
