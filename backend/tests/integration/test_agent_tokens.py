"""A member issues a credential for their OWN agent, and uses it.

The dogfooding failure this closes: an agent a member ran themselves had no way
to hold a credential, so it borrowed the human's session token and became the
human — same author on every block, and @-ing it was @-ing yourself, which the
mention path drops. These tests walk the whole loop over real HTTP: issue, use,
be seen as someone distinct, be notified, be bounded, be revoked.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.agent_tokens import TOKEN_PREFIX, hash_token
from tests.conftest import seed_user


def _query(client, statement: str, **params) -> list[tuple]:
    """Read stored rows directly — used where the assertion is about what the
    database holds, which no endpoint will ever show you."""

    async def _run() -> list[tuple]:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            result = await session.execute(text(statement), params)
            return list(result.all())

    return asyncio.run(_run())


def _execute(client, statement: str, **params) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await session.execute(text(statement), params)
            await session.commit()

    asyncio.run(_run())


def _human(client, username: str) -> dict[str, str]:
    """Headers for ``username`` acting as themselves. Issuing needs a real user
    row (a token hangs off an int user id), which ``seed_user`` provides."""
    return {"Authorization": f"Bearer {seed_user(client, username)}"}


def _agent(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _issue(client, headers: dict[str, str], **body) -> dict:
    r = client.post(
        "/api/agent-tokens", json=body or {"name": "laptop"}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _project_and_topic(client, owner: str) -> tuple[str, str]:
    project_id = client.post(
        "/api/projects", json={"name": "P", "owner_handle": owner}
    ).json()["data"]["id"]
    topic_id = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": "T", "created_by": owner},
    ).json()["data"]["id"]
    return project_id, topic_id


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]


def _post_over_ws(client, topic_id: str, content: str, token: str) -> None:
    with client.websocket_connect(f"/api/topics/{topic_id}/chat?token={token}") as ws:
        ws.send_json({"type": "message", "content": content, "summon": False})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                return


# --- issuing -------------------------------------------------------------


def test_the_secret_is_shown_once_and_the_database_never_holds_it(client):
    """A leaked database must not be a leaked credential, so only the digest is
    stored — which also means the list can never hand the secret back."""
    headers = _human(client, "alice")
    issued = _issue(client, headers)

    assert issued["token"].startswith(TOKEN_PREFIX)
    assert issued["agentHandle"] == "alice-agent"

    stored = _query(
        client,
        "SELECT token_hash, token_prefix FROM agent_tokens WHERE id = :id",
        id=uuid.UUID(issued["id"]),
    )
    assert stored[0][0] == hash_token(issued["token"])
    assert issued["token"] not in stored[0]

    listed = client.get("/api/agent-tokens", headers=headers).json()["data"]["data"]
    assert len(listed) == 1
    assert "token" not in listed[0]
    # Enough of the head survives to tell two tokens apart in the UI.
    assert issued["token"].startswith(listed[0]["tokenPrefix"])


def test_an_agent_cannot_mint_itself_more_credentials(client):
    """Issuing is a human act. If the agent could issue, revoking one token would
    not end the delegation — it would just be the previous key."""
    issued = _issue(client, _human(client, "alice"))
    r = client.post(
        "/api/agent-tokens", json={"name": "second"}, headers=_agent(issued["token"])
    )
    assert r.status_code == 401


def test_only_the_issuer_sees_or_revokes_their_tokens(client):
    alice, bob = _human(client, "alice"), _human(client, "bob")
    issued = _issue(client, alice)

    assert client.get("/api/agent-tokens", headers=bob).json()["data"]["data"] == []
    assert (
        client.delete(f"/api/agent-tokens/{issued['id']}", headers=bob).status_code
        == 404
    )
    # …and alice's token still works after bob's attempt.
    assert _whoami(client, issued["token"])["handle"] == "alice-agent"


# --- authenticating ------------------------------------------------------


def _whoami(client, secret: str) -> dict:
    return client.get("/api/agent-tokens/whoami", headers=_agent(secret)).json()["data"]


def test_the_token_authenticates_as_the_agent_on_behalf_of_the_human(client):
    issued = _issue(client, _human(client, "alice"))
    who = _whoami(client, issued["token"])
    assert who == {
        "handle": "alice-agent",
        "userId": who["userId"],
        "isAgent": True,
        "via": "agent",
        "ownerHandle": "alice",
        "authenticated": True,
    }


def test_the_agents_messages_carry_its_own_handle_not_the_humans(client):
    """The whole point: reading the timeline tells you the agent spoke, without
    fingerprinting the prose."""
    issued = _issue(client, _human(client, "alice"))
    _, topic_id = _project_and_topic(client, owner="alice")

    _post_over_ws(client, topic_id, "从我的 agent 发的", issued["token"])

    authored = {b["content"]: b["author"] for b in _blocks(client, topic_id)}
    assert authored["从我的 agent 发的"] == "alice-agent"


def test_the_platform_agent_keeps_authoring_the_rooms_ai_blocks(client):
    """A member's agent taking a roster seat must not make it the room's 芝士 —
    it would silently take over authorship of every AI reply."""
    issued = _issue(client, _human(client, "alice"))
    _, topic_id = _project_and_topic(client, owner="alice")
    r = client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": issued["agentHandle"], "role": "member", "actor": "alice"},
    )
    assert r.status_code == 200

    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": "hi", "author": "alice", "summon": True}
        )
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break

    ai_authors = {
        b["author"] for b in _blocks(client, topic_id) if b["author_type"] == "ai"
    }
    assert ai_authors
    assert issued["agentHandle"] not in ai_authors


# --- permission ceiling --------------------------------------------------


def test_the_agent_is_refused_where_its_owner_would_be(client):
    """越权: alice's agent has no business in bob's project."""
    _human(client, "bob")
    issued = _issue(client, _human(client, "alice"))
    _, topic_id = _project_and_topic(client, owner="bob")

    r = client.put(
        f"/api/topics/{topic_id}/doc",
        json={"content": "# sneaky"},
        headers=_agent(issued["token"]),
    )
    assert r.status_code == 403


def test_the_agent_is_allowed_where_its_owner_is(client):
    issued = _issue(client, _human(client, "alice"))
    _, topic_id = _project_and_topic(client, owner="alice")

    r = client.put(
        f"/api/topics/{topic_id}/doc",
        json={"content": "# from the agent"},
        headers=_agent(issued["token"]),
    )
    assert r.status_code == 200
    assert r.json()["data"]["author"] == "alice-agent"


def test_the_agent_cannot_change_who_else_gets_in(client):
    """The project roster is the floor of topic access, so an agent writing to it
    could widen its own reach. Refused for the same reason 芝士 is."""
    issued = _issue(client, _human(client, "alice"))
    project_id, _ = _project_and_topic(client, owner="alice")

    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "mallory", "role": "member"},
        headers=_agent(issued["token"]),
    )
    assert r.status_code in (401, 403, 404)


# --- notifications -------------------------------------------------------


def _notifications(client, project_id: str, handle: str) -> list[dict]:
    return client.get(
        f"/api/projects/{project_id}/notifications?target_handle={handle}"
    ).json()["data"]["data"]


def _add_project_member(client, project_id: str, handle: str, owner) -> None:
    """Seat ``handle`` in the project roster — <@handle> resolves against it."""
    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": handle, "role": "member"},
        headers=owner,
    )
    assert r.status_code == 200, r.text


def test_mentioning_your_own_agent_notifies_it(client):
    """Used to be impossible: with one shared account the mention was self-@, and
    the "nobody is notified of their own message" filter dropped it."""
    alice = _human(client, "alice")
    issued = _issue(client, alice)
    project_id, topic_id = _project_and_topic(client, owner="alice")
    _add_project_member(client, project_id, issued["agentHandle"], alice)

    _post_over_ws_as_human(
        client, topic_id, f"<@{issued['agentHandle']}> 去看看 CI", alice
    )

    got = _notifications(client, project_id, issued["agentHandle"])
    assert len(got) == 1
    assert "CI" in got[0]["body"]


def _post_over_ws_as_human(client, topic_id: str, content: str, headers) -> None:
    token = headers["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(f"/api/topics/{topic_id}/chat?token={token}") as ws:
        ws.send_json({"type": "message", "content": content, "summon": False})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                return


def test_a_broadcast_still_reaches_a_members_own_agent(client):
    """@all skips 芝士 because it reads the room anyway. A member's own agent does
    not, so skipping it would silently drop the message."""
    alice = _human(client, "alice")
    issued = _issue(client, alice)
    project_id, topic_id = _project_and_topic(client, owner="alice")
    _add_project_member(client, project_id, issued["agentHandle"], alice)
    r = client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": issued["agentHandle"], "role": "member", "actor": "alice"},
    )
    assert r.status_code == 200, r.text

    _post_over_ws_as_human(client, topic_id, "<@all> 都看一下", alice)

    assert len(_notifications(client, project_id, issued["agentHandle"])) == 1


# --- ending access -------------------------------------------------------


def test_revoking_ends_access_immediately(client):
    headers = _human(client, "alice")
    issued = _issue(client, headers)
    assert _whoami(client, issued["token"])["authenticated"] is True

    r = client.delete(f"/api/agent-tokens/{issued['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["revokedAt"] is not None

    after = _whoami(client, issued["token"])
    assert after["authenticated"] is False
    assert after["handle"] != "alice-agent"


def test_an_expired_token_stops_authenticating(client):
    """The credential lives on someone's laptop; a forgotten one has to die by
    itself rather than waiting for a human to remember it."""
    issued = _issue(client, _human(client, "alice"), name="short", expiresInDays=1)
    _execute(
        client,
        "UPDATE agent_tokens SET expires_at = :past WHERE id = :id",
        past=datetime.now(UTC) - timedelta(seconds=1),
        id=uuid.UUID(issued["id"]),
    )

    assert _whoami(client, issued["token"])["authenticated"] is False


def test_reissuing_keeps_the_same_agent_identity(client):
    """Rotating the credential must not fork the identity — the history a room
    already shows is authored by that handle."""
    headers = _human(client, "alice")
    first = _issue(client, headers, name="old")
    client.delete(f"/api/agent-tokens/{first['id']}", headers=headers)
    second = _issue(client, headers, name="new")

    assert second["agentHandle"] == first["agentHandle"]
    assert _whoami(client, second["token"])["handle"] == first["agentHandle"]


@pytest.mark.parametrize("days", [0, 400])
def test_an_absurd_lifetime_is_refused(client, days: int):
    """A never-expiring credential is the one this design cannot allow, and a
    zero-day one is a typo, not a request."""
    r = client.post(
        "/api/agent-tokens",
        json={"name": "x", "expiresInDays": days},
        headers=_human(client, "alice"),
    )
    assert r.status_code == 400
    # Refused for the lifetime specifically, not for some unrelated reason.
    details = r.json()["error"]["data"]["details"]
    assert [d["loc"][-1] for d in details] == ["expiresInDays"]
