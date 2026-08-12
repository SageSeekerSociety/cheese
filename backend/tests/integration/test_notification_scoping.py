"""A project mailbox belongs to one person.

Dogfooding an agent onto the platform surfaced the opposite: the project
notification endpoints treated ``target_handle`` as an optional filter, so a
caller who omitted it read everyone's mail — and ``read-all`` cleared everyone's
unread state. These tests pin the recipient to whoever is calling.

Half of them exist because the first attempt at that fix did not hold. It
resolved the recipient through the Phase-0 handle fallback, which made the query
string authenticate itself: dropping the ``Authorization`` header turned
``?target_handle=bob`` back into "read bob's mail", and the 403 guarding it
could never fire because such a caller is never authenticated. So every case
below is written twice over — once for a caller with the wrong identity, once
for a caller with none at all — and the aggregates and per-item actions that
serve the same rows are covered alongside the list.
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import session_auth_headers

# A syntactically fine bearer token that verifies to nothing — an expired or
# revoked session, and the cheapest way to reach the unauthenticated path while
# still looking like a logged-in client.
STALE_TOKEN = {"Authorization": "Bearer not-a-real-token"}


def _project(client, name: str = "Mailbox") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _topic(client, project_id: str, title: str = "话题") -> str:
    r = client.post("/api/topics", json={"project_id": project_id, "title": title})
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
    body: str = "",
    payload: dict | None = None,
    topic_id: str | None = None,
) -> dict:
    r = client.post(
        f"/api/projects/{project_id}/notifications",
        json={
            "level": level,
            "kind": kind,
            "title": title,
            "target_handle": target,
            "body": body,
            "payload": payload or {},
            "topic_id": topic_id,
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


def test_naming_a_mailbox_without_a_credential_does_not_open_it(client):
    """The one the first fix missed: no header at all, plus a named handle."""
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")
    _notify(client, pid, "全体注意")

    for headers in ({}, STALE_TOKEN):
        for path in (
            f"/api/projects/{pid}/notifications",
            f"/api/projects/{pid}/inbox",
        ):
            r = client.get(path, params={"target_handle": "bob"}, headers=headers)
            assert r.status_code == 200, (path, headers, r.text)
            assert "给bob" not in _titles(r), (path, headers, _titles(r))


def test_read_all_without_a_credential_cannot_clear_a_named_mailbox(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")
    assert _unread(client, pid, "bob") == 1

    for headers in ({}, STALE_TOKEN):
        r = client.post(
            f"/api/projects/{pid}/notifications/read-all",
            params={"target_handle": "bob"},
            headers=headers,
        )
        assert r.status_code == 401, (headers, r.text)
    assert _unread(client, pid, "bob") == 1


def test_read_all_and_unread_count_refuse_another_persons_mailbox(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    alice = session_auth_headers("alice")
    r = client.post(
        f"/api/projects/{pid}/notifications/read-all",
        params={"target_handle": "bob"},
        headers=alice,
    )
    assert r.status_code == 403, r.text
    r = client.get(
        f"/api/projects/{pid}/notifications/unread-count",
        params={"target_handle": "bob"},
        headers=alice,
    )
    assert r.status_code == 403, r.text
    assert _unread(client, pid, "bob") == 1


def test_the_overview_board_shows_the_caller_their_own_pending_items(client):
    """The same rows, reached through the aggregate instead of the list.

    ``/overview`` grouped the whole project's inbox by handle with no auth at
    all, which both published everyone's pending decisions and handed out the
    ids the per-notification routes were then acted on with.
    """
    pid = _project(client)
    _notify(client, pid, "bob拍板", target="bob", kind="decision_request")
    _notify(client, pid, "alice拍板", target="alice", kind="decision_request")
    _notify(client, pid, "谁来都行", kind="decision_request")

    anon = client.get(f"/api/projects/{pid}/overview")
    assert anon.status_code == 200
    waiting = anon.json()["data"]["waiting_on_you"]
    assert "bob" not in waiting and "alice" not in waiting
    assert [i["title"] for i in waiting.get("未分派", [])] == ["谁来都行"]

    mine = client.get(
        f"/api/projects/{pid}/overview", headers=session_auth_headers("alice")
    ).json()["data"]["waiting_on_you"]
    assert [i["title"] for i in mine["alice"]] == ["alice拍板"]
    assert "bob" not in mine


def test_a_member_page_does_not_hand_out_that_members_mailbox(client):
    pid = _project(client)
    _notify(client, pid, "bob拍板", target="bob", kind="decision_request")
    _notify(client, pid, "谁来都行", kind="decision_request")

    def waiting(headers: dict[str, str]) -> set[str]:
        r = client.get(f"/api/projects/{pid}/members/bob/summary", headers=headers)
        assert r.status_code == 200, r.text
        return {w["title"] for w in r.json()["data"]["waiting_on_you"]}

    # bob's own page shows bob's items; everyone else sees only what waits on
    # the whole project.
    assert waiting(session_auth_headers("bob")) == {"bob拍板", "谁来都行"}
    assert waiting(session_auth_headers("alice")) == {"谁来都行"}
    assert waiting({}) == {"谁来都行"}


def test_only_the_recipient_may_read_one_notification(client):
    pid = _project(client)
    n = _notify(client, pid, "给bob", target="bob", body="机密内容")

    for headers in ({}, STALE_TOKEN):
        r = client.post(f"/api/notifications/{n['id']}/read", headers=headers)
        assert r.status_code == 401, (headers, r.text)
        assert "机密内容" not in r.text

    r = client.post(
        f"/api/notifications/{n['id']}/read", headers=session_auth_headers("alice")
    )
    assert r.status_code == 403, r.text
    assert "机密内容" not in r.text

    # Still unread after all of that — /read writes read_at on the row itself,
    # so a refused caller must not have moved bob's badge.
    assert _unread(client, pid, "bob") == 1

    r = client.post(
        f"/api/notifications/{n['id']}/read", headers=session_auth_headers("bob")
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["body"] == "机密内容"
    assert _unread(client, pid, "bob") == 0


def test_only_the_recipient_may_rate_one_notification(client):
    pid = _project(client)
    n = _notify(client, pid, "给bob", target="bob")

    r = client.post(f"/api/notifications/{n['id']}/feedback", json={"feedback": "down"})
    assert r.status_code == 401, r.text
    r = client.post(
        f"/api/notifications/{n['id']}/feedback",
        json={"feedback": "down"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 403, r.text

    r = client.post(
        f"/api/notifications/{n['id']}/feedback",
        json={"feedback": "down"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["feedback"] == "down"


def test_only_the_recipient_may_settle_a_decision_and_the_decider_is_the_caller(client):
    pid = _project(client)
    tid = _topic(client, pid)
    n = _notify(
        client,
        pid,
        "选哪个",
        target="bob",
        level="strong",
        kind="decision_request",
        payload={"options": ["A", "B"]},
        topic_id=tid,
    )

    def blocks() -> list[dict]:
        return client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]

    r = client.post(f"/api/notifications/{n['id']}/resolve", json={"chosen": "A"})
    assert r.status_code == 401, r.text
    r = client.post(
        f"/api/notifications/{n['id']}/resolve",
        json={"chosen": "A"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403, r.text
    assert not [b for b in blocks() if "【决策】" in b["content"]]

    # bob decides — and the decision is recorded as bob's no matter what the
    # body claims, because that block is what 芝士 acts on next turn.
    r = client.post(
        f"/api/notifications/{n['id']}/resolve",
        json={"chosen": "A", "decided_by": "mallory"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200, r.text
    decisions = [b for b in blocks() if "【决策】" in b["content"]]
    assert [b["author"] for b in decisions] == ["bob"]


def test_topic_unread_badges_belong_to_the_caller(client):
    pid = _project(client)
    tid = _topic(client, pid)

    async def _seed(author: str) -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(pid),
                topic_id=uuid.UUID(tid),
                author=author,
                author_type=AuthorType.human,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    # Two messages, one of them bob's own — so bob's badge (1) differs from an
    # unidentified caller's (2), which is what makes the scoping observable.
    asyncio.run(_seed("cheese"))
    asyncio.run(_seed("bob"))

    def unread(headers: dict[str, str], params: dict[str, str] | None = None):
        return client.get(
            f"/api/projects/{pid}/topic-unread", params=params, headers=headers
        )

    # bob's own badge, with and without spelling his handle out.
    bob = session_auth_headers("bob")
    assert unread(bob).json()["data"].get(tid) == 1
    assert unread(bob, {"handle": "bob"}).json()["data"].get(tid) == 1

    # Naming bob buys nothing: refused when the caller is someone else, and
    # ignored (nobody's badge, not bob's) when the caller is nobody.
    assert unread(session_auth_headers("alice"), {"handle": "bob"}).status_code == 403
    r = unread({}, {"handle": "bob"})
    assert r.status_code == 200
    assert r.json()["data"].get(tid) == 2
