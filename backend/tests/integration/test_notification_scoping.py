"""A project mailbox belongs to one person.

Dogfooding an agent onto the platform surfaced the opposite: the project
notification endpoints treated ``target_handle`` as an optional filter, so a
caller who omitted it read everyone's mail — and ``read-all`` cleared everyone's
unread state. These tests pin the recipient to whoever is calling.
"""

import asyncio
import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import session_auth_headers, session_token


def _project(client, name: str = "Mailbox") -> str:
    r = client.post("/projects", json={"name": name})
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
        f"/projects/{project_id}/alerts",
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
        f"/projects/{project_id}/alerts/unread-count",
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["unread"]


def test_list_serves_the_caller_their_own_mail_and_broadcasts(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    _notify(client, pid, "给bob", target="bob")
    _notify(client, pid, "全体注意")

    r = client.get(f"/projects/{pid}/alerts", headers=session_auth_headers("alice"))
    assert r.status_code == 200
    assert _titles(r) == ["全体注意", "给alice"]
    assert r.json()["data"]["total"] == 2


def test_reading_someone_elses_mailbox_is_refused(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    for path in (f"/projects/{pid}/alerts", f"/projects/{pid}/inbox"):
        r = client.get(
            path,
            params={"target_handle": "bob"},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 403, (path, r.text)

    # ...and the caller's own handle is of course fine.
    r = client.get(
        f"/projects/{pid}/alerts",
        params={"target_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200


def test_read_all_leaves_other_peoples_notifications_unread(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    bobs = _notify(client, pid, "给bob", target="bob")

    r = client.post(
        f"/projects/{pid}/alerts/read-all",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["marked"] == 1

    assert _unread(client, pid, "alice") == 0
    assert _unread(client, pid, "bob") == 1
    still_unread = client.get(
        f"/projects/{pid}/alerts", headers=session_auth_headers("bob")
    )
    assert [n["read_at"] for n in still_unread.json()["data"]["data"]] == [None]
    assert still_unread.json()["data"]["data"][0]["id"] == bobs["id"]


def test_read_all_refuses_a_caller_who_names_nobody(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")

    r = client.post(f"/projects/{pid}/alerts/read-all")
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

    r = client.get(f"/projects/{pid}/inbox", headers=session_auth_headers("alice"))
    assert _titles(r) == ["alice拍板"]


def test_an_unidentified_caller_sees_broadcasts_only(client):
    pid = _project(client)
    _notify(client, pid, "给alice", target="alice")
    _notify(client, pid, "全体注意")

    r = client.get(f"/projects/{pid}/alerts")
    assert _titles(r) == ["全体注意"]


# ---- Naming a handle is an assertion, never an identity -----------------------
# The original hole: dropping the Authorization header (or presenting a token
# that fails verification) used to make ?target_handle=<anyone> a free pass —
# the fallback resolved the query string itself as the recipient, so the
# authenticated-mismatch 403 never fired. These pin the whole matrix.


def test_tokenless_caller_naming_someone_else_is_refused(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")
    _notify(client, pid, "全体注意")

    for path in (
        f"/projects/{pid}/alerts",
        f"/projects/{pid}/inbox",
        f"/projects/{pid}/alerts/unread-count",
    ):
        r = client.get(path, params={"target_handle": "bob"})
        assert r.status_code == 401, (path, r.text)
        assert "给bob" not in r.text


def test_bad_token_naming_someone_else_is_refused(client):
    """A token that fails verification must not degrade into the handle
    fallback — that would make stripping to a garbage header equivalent to no
    auth at all."""
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    for token in ("garbage-not-a-jwt", session_token("bob", ttl_s=-1)):
        r = client.get(
            f"/projects/{pid}/alerts",
            params={"target_handle": "bob"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401, r.text
        assert "给bob" not in r.text


def test_bad_token_is_refused_even_without_naming_anyone(client):
    """A presented-but-invalid credential is an error, not anonymous browsing —
    silent downgrade is exactly what made the header strippable."""
    pid = _project(client)
    _notify(client, pid, "全体注意")

    r = client.get(
        f"/projects/{pid}/alerts",
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 401, r.text


def test_read_all_never_clears_a_named_strangers_mailbox(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    # Anonymous, naming bob → 401, nothing marked.
    r = client.post(
        f"/projects/{pid}/alerts/read-all",
        params={"target_handle": "bob"},
    )
    assert r.status_code == 401, r.text
    assert _unread(client, pid, "bob") == 1

    # Bad token, naming bob → 401, nothing marked.
    r = client.post(
        f"/projects/{pid}/alerts/read-all",
        params={"target_handle": "bob"},
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 401, r.text
    assert _unread(client, pid, "bob") == 1

    # Authenticated as alice, naming bob → 403, nothing marked.
    r = client.post(
        f"/projects/{pid}/alerts/read-all",
        params={"target_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 403, r.text
    assert _unread(client, pid, "bob") == 1


def test_unread_count_refuses_someone_elses_badge(client):
    pid = _project(client)
    _notify(client, pid, "给bob", target="bob")

    r = client.get(
        f"/projects/{pid}/alerts/unread-count",
        params={"target_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 403, r.text


# ---- Per-notification actions (/read /feedback /resolve) ----------------------


def _decision(client, project_id: str, title: str, target: str) -> dict:
    r = client.post(
        f"/projects/{project_id}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": title,
            "body": "机密内容",
            "target_handle": target,
            "payload": {"options": ["A", "B"]},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_notification_actions_require_a_verified_caller(client):
    pid = _project(client)
    n = _decision(client, pid, "alice拍板", target="alice")

    r = client.post(f"/alerts/{n['id']}/read")
    assert r.status_code == 401, r.text
    assert "机密内容" not in r.text
    r = client.post(f"/alerts/{n['id']}/feedback", json={"feedback": "up"})
    assert r.status_code == 401, r.text
    r = client.post(
        f"/alerts/{n['id']}/resolve",
        json={"chosen": "B", "decided_by": "mallory"},
    )
    assert r.status_code == 401, r.text

    # None of the refused attempts changed anything.
    mine = client.get(
        f"/projects/{pid}/alerts", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [(x["read_at"], x["resolved_at"], x["feedback"]) for x in mine] == [
        (None, None, None)
    ]


def test_notification_actions_refuse_a_non_recipient(client):
    pid = _project(client)
    n = _decision(client, pid, "alice拍板", target="alice")
    bob = session_auth_headers("bob")

    r = client.post(f"/alerts/{n['id']}/read", headers=bob)
    assert r.status_code == 403, r.text
    assert "机密内容" not in r.text
    r = client.post(f"/alerts/{n['id']}/feedback", json={"feedback": "up"}, headers=bob)
    assert r.status_code == 403, r.text
    r = client.post(f"/alerts/{n['id']}/resolve", json={"chosen": "B"}, headers=bob)
    assert r.status_code == 403, r.text


def test_resolve_attributes_the_decision_to_the_verified_caller(client):
    pid = _project(client)
    tid = client.post("/topics", json={"project_id": pid, "title": "T"}).json()["data"][
        "id"
    ]
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "拍板",
            "target_handle": "alice",
            "topic_id": tid,
            "payload": {"options": ["A", "B"]},
        },
    )
    n = r.json()["data"]

    # The body claims mallory decided; the block must carry alice.
    r = client.post(
        f"/alerts/{n['id']}/resolve",
        json={"chosen": "A", "decided_by": "mallory"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    decision = next(b for b in blocks if "【决策】" in b["content"])
    assert decision["author"] == "alice"


# ---- The overview board no longer republishes everyone's mailbox --------------


def test_overview_requires_auth_and_shows_only_the_callers_items(client):
    pid = _project(client)
    _decision(client, pid, "alice的事", target="alice")
    _decision(client, pid, "bob的事", target="bob")
    r = client.post(
        f"/projects/{pid}/alerts",
        json={
            "level": "strong",
            "kind": "accept_request",
            "title": "大家的事",
        },
    )
    assert r.status_code == 200, r.text

    # Anonymous → 401, and no titles/ids leak.
    r = client.get(f"/projects/{pid}/overview")
    assert r.status_code == 401, r.text
    assert "bob的事" not in r.text

    # Alice sees her own slice + broadcasts — never bob's.
    ov = client.get(
        f"/projects/{pid}/overview", headers=session_auth_headers("alice")
    ).json()["data"]
    waiting = ov["waiting_on_you"]
    assert [x["title"] for x in waiting.get("alice", [])] == ["alice的事"]
    assert "bob" not in waiting
    flat = [x["title"] for items in waiting.values() for x in items]
    assert "大家的事" in flat and "bob的事" not in flat


# ---- topic-unread: same rule, same layer --------------------------------------


def _topic(client, project_id: str, title: str = "T") -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _add_member(client, project_id: str, handle: str) -> None:
    """Membership for the guard added 2026-08-16: these tests' handles are
    participants by intent, and the topic routes now deny outsiders."""
    import uuid as _uuid

    from app.domain.project.models import ProjectMember

    async def _run() -> None:
        async with client.test_factory() as session:
            session.add(
                ProjectMember(project_id=_uuid.UUID(project_id), user_handle=handle)
            )
            await session.commit()

    asyncio.run(_run())


def _seed_message(client, project_id: str, topic_id: str, author: str) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                topic_id=uuid.UUID(topic_id),
                author=author,
                author_type=AuthorType.human,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    asyncio.run(_run())


def _topic_unread(client, project_id: str, handle: str) -> dict:
    r = client.get(
        f"/projects/{project_id}/topic-unread",
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---- The member page no longer hands out that member's mailbox ----------------


def test_a_member_page_does_not_hand_out_that_members_mailbox(client):
    """Sibling of the /overview hole: /members/{handle}/summary read the NAMED
    member's inbox with no auth at all, so anyone could harvest anybody's
    pending decisions (titles + ids) by naming them in the URL."""
    pid = _project(client)
    _notify(client, pid, "bob拍板", target="bob", kind="decision_request")
    _notify(client, pid, "谁来都行", kind="decision_request")

    # Anonymous → 401, and nothing of bob's leaks.
    r = client.get(f"/projects/{pid}/members/bob/summary")
    assert r.status_code == 401, r.text
    assert "bob拍板" not in r.text

    # A presented-but-invalid credential is 401, not anonymous browsing.
    r = client.get(
        f"/projects/{pid}/members/bob/summary",
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 401, r.text
    assert "bob拍板" not in r.text

    def waiting(handle: str) -> set[str]:
        r = client.get(
            f"/projects/{pid}/members/bob/summary",
            headers=session_auth_headers(handle),
        )
        assert r.status_code == 200, r.text
        return {w["title"] for w in r.json()["data"]["waiting_on_you"]}

    # bob's own page shows bob's items; a teammate sees only the broadcasts.
    assert waiting("bob") == {"bob拍板", "谁来都行"}
    assert waiting("alice") == {"谁来都行"}


# ---- topic read-cursor: identity comes from the credential, not the body ------


def test_topic_read_cursor_belongs_to_the_verified_caller(client):
    """POST /topics/{id}/read trusted ``body.handle`` as the identity, so anyone
    could silently clear anyone else's unread badge."""
    pid = _project(client)
    tid = _topic(client, pid)
    for h in ("alice", "bob"):
        _add_member(client, pid, h)
    _seed_message(client, pid, tid, "cheese")
    assert _topic_unread(client, pid, "bob").get(tid) == 1

    url = f"/topics/{tid}/read"

    # Anonymous naming bob → 401; bad token naming bob → 401.
    r = client.post(url, json={"handle": "bob"})
    assert r.status_code == 401, r.text
    r = client.post(
        url,
        json={"handle": "bob"},
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 401, r.text

    # Authenticated as alice, naming bob → 403, cursor unmoved.
    r = client.post(url, json={"handle": "bob"}, headers=session_auth_headers("alice"))
    assert r.status_code == 403, r.text
    assert _topic_unread(client, pid, "bob").get(tid) == 1

    # bob himself (asserting his own handle) clears his badge — nobody else's.
    _seed_message(client, pid, tid, "cheese")
    r = client.post(url, json={"handle": "bob"}, headers=session_auth_headers("bob"))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["handle"] == "bob"
    assert tid not in _topic_unread(client, pid, "bob")
    assert _topic_unread(client, pid, "alice").get(tid) == 2


# ---- creating a notification needs a credential the route itself checks -------


def _create_body(title: str = "x") -> dict:
    return {"level": "light", "kind": "change_alert", "title": title}


def test_creating_a_notification_requires_a_credential(client):
    """The route used to rely on the middleware gate alone; now it refuses the
    unauthenticated itself. The TestClient sends the global sandbox token on
    every request, so the bare-call cases strip it explicitly."""
    pid = _project(client)
    url = f"/projects/{pid}/alerts"

    # Bare call — no cheese token, no bearer → 401, nothing created.
    r = client.post(url, json=_create_body(), headers={"X-Cheese-Token": ""})
    assert r.status_code == 401, r.text

    # A wrong cheese token is not "no token" — still 401.
    r = client.post(
        url, json=_create_body(), headers={"X-Cheese-Token": "not-the-secret"}
    )
    assert r.status_code == 401, r.text

    # A presented-but-invalid bearer must not degrade into anonymous.
    for bad in ("garbage-not-a-jwt", session_token("alice", ttl_s=-1)):
        r = client.post(
            url,
            json=_create_body(),
            headers={"X-Cheese-Token": "", "Authorization": f"Bearer {bad}"},
        )
        assert r.status_code == 401, r.text

    # None of the refused attempts landed.
    r = client.get(url, headers=session_auth_headers("alice"))
    assert r.json()["data"]["total"] == 0


def test_creating_with_a_bearer_alone_works(client):
    pid = _project(client)
    r = client.post(
        f"/projects/{pid}/alerts",
        json=_create_body("人发的"),
        headers={"X-Cheese-Token": "", **session_auth_headers("alice")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title"] == "人发的"


def test_creating_with_a_scoped_token_works(client):
    """The sandbox cheese CLI path: a per-turn token scoped to this project."""
    pid = _project(client)
    tid = _topic(client, pid)
    for token, body in (
        (
            mint_scoped_token(project_id=pid, topic_id=tid, access_scope="project"),
            _create_body(),
        ),
        (
            mint_scoped_token(project_id=pid, topic_id=tid),
            {**_create_body(), "topic_id": tid},
        ),
    ):
        r = client.post(
            f"/projects/{pid}/alerts",
            json=body,
            headers={"X-Cheese-Token": token},
        )
        assert r.status_code == 200, r.text


def test_creating_alerts_requires_a_named_agent_and_matching_room(client):
    pid = _project(client)
    tid = _topic(client, pid)
    other = _topic(client, pid)
    for token, body, expected in (
        (mint_scoped_token(project_id=pid), _create_body(), 401),
        (mint_scoped_token(project_id=pid, topic_id=tid), _create_body(), 403),
        (
            mint_scoped_token(project_id=pid, topic_id=tid),
            {**_create_body(), "topic_id": other},
            403,
        ),
    ):
        response = client.post(
            f"/projects/{pid}/alerts", json=body, headers={"X-Cheese-Token": token}
        )
        assert response.status_code == expected, response.text


def test_a_scoped_token_for_another_project_cannot_notify_here(client):
    pid = _project(client)
    other = _project(client, "Other")
    r = client.post(
        f"/projects/{pid}/alerts",
        json=_create_body(),
        headers={"X-Cheese-Token": mint_scoped_token(project_id=other)},
    )
    assert r.status_code == 403, r.text


def test_topic_unread_refuses_an_unverified_or_mismatched_handle(client):
    pid = _project(client)

    r = client.get(f"/projects/{pid}/topic-unread", params={"handle": "bob"})
    assert r.status_code == 401, r.text

    r = client.get(
        f"/projects/{pid}/topic-unread",
        params={"handle": "bob"},
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 401, r.text

    r = client.get(
        f"/projects/{pid}/topic-unread",
        params={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 403, r.text

    # The caller's own map works, with or without naming themselves.
    r = client.get(
        f"/projects/{pid}/topic-unread", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
