"""P1 真鉴权 + agent-as-user over HTTP: login mints a token, the actor is
resolved from the verified token (not the body), the Phase-0 handle fallback
still works, and a token-authenticated outsider is denied (越权)."""

import pytest

from app.core.tokens import verify_session_token
from tests.integration.conftest import session_token


def _login(client, handle: str) -> str:
    # The cheesex Phase-0 handle-login endpoint (POST /api/users/login) was retired
    # in the fusion merge (unify P3: auth unified to main's SRP login). The actor
    # here is identified purely by the token's ``handle`` claim, so mint a session
    # token directly to exercise the (still-present) token-actor resolution.
    return session_token(handle)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": owner},
    ).json()["data"]
    return p["id"], t["id"]


def test_login_returns_verifiable_token(client):
    token = _login(client, "alice")
    claims = verify_session_token(token)
    assert claims is not None
    assert claims["sub"] == "alice"


def test_token_actor_wins_over_body_author(client):
    """A doc edit's author comes from the VERIFIED token, never the body field —
    a forged `author` is ignored."""
    token = _login(client, "alice")
    _, tid = _project_topic(client, owner="alice")
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# hi", "author": "mallory-forged"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    assert r.json()["data"]["author"] == "alice"  # token, not the body


def test_no_token_falls_back_to_body_author(client):
    """Existing (pre-token) callers keep working: no Authorization header → the
    body's author is honored."""
    _login(client, "alice")
    _, tid = _project_topic(client, owner="alice")
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# hi", "author": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["author"] == "alice"


def test_token_outsider_denied_on_rostered_topic(client):
    """越权: a token-authenticated user who is neither a topic member nor a
    project member is denied on a topic that has a roster."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login(client, "mallory")
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# sneaky", "author": "mallory"},
        headers=_bearer(outsider),
    )
    assert r.status_code == 403


def test_token_owner_allowed(client):
    """The topic owner (in the roster) may edit its doc with their token."""
    token = _login(client, "alice")
    _, tid = _project_topic(client, owner="alice")
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# ok", "author": "alice"},
        headers=_bearer(token),
    )
    assert r.status_code == 200


def test_ws_token_pins_message_author(client):
    """On the chat WS the author comes from the connection's ?token=, so a forged
    per-message `author` is ignored."""
    token = _login(client, "alice")
    _, tid = _project_topic(client, owner="alice")
    with client.websocket_connect(f"/topics/{tid}/chat?token={token}") as ws:
        ws.send_json(
            {"type": "message", "content": "hello", "author": "mallory-forged"}
        )
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("done", "error"):
                break
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    users = [b for b in blocks if b["content"] == "hello"]
    assert users and all(b["author"] == "alice" for b in users)


def test_ws_outsider_token_rejected(client):
    """越权: an outsider's token on the chat WS is refused before any message."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login(client, "mallory")
    with client.websocket_connect(f"/topics/{tid}/chat?token={outsider}") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"


def test_ensure_agent_user_idempotent_and_derives_agent(client):
    """芝士 is a real agent-user; re-seeding never duplicates and is_agent is
    derived from the binding."""
    import asyncio

    from app.domain.identity.services import IdentityService

    async def _check() -> None:
        async with client.test_factory() as s:  # type: ignore[attr-defined]
            svc = IdentityService(s)
            u1 = await svc.ensure_agent_user()
            u2 = await svc.ensure_agent_user()  # idempotent
            assert u1.id == u2.id
            assert await svc.is_agent("cheese") is True
            assert await svc.is_agent("alice") is False

    asyncio.run(_check())


def test_project_member_allowed_even_if_not_in_roster(client):
    """权限属于项目: a project member may act in the project's topics even without
    being on that topic's roster."""
    import asyncio
    import uuid

    from app.domain.identity.actor import Actor
    from app.domain.membership.services import MemberService
    from app.domain.project.models import ProjectRole

    pid, tid = _project_topic(client, owner="alice")
    token = _login(client, "bob")

    # Seed bob as a project member directly (StaticPool shares the connection).
    async def _add_member() -> None:
        async with client.test_factory() as s:  # type: ignore[attr-defined]
            await MemberService(s).add(
                project_id=uuid.UUID(pid),
                user_handle="bob",
                role=ProjectRole.member,
                # Roster writes are authorized — seed as the project owner.
                actor=Actor(handle="alice", user_id=None, is_agent=False, via="token"),
            )
            await s.commit()

    asyncio.run(_add_member())

    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# member", "author": "bob"},
        headers=_bearer(token),
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/topics/{tid}/doc",
        "/topics/{tid}/docs",
        "/topics/{tid}/comments",
        "/topics/{tid}/transcript",
        "/topics/{tid}/status",
        "/topics/{tid}/progress",
        "/topics/{tid}/children",
    ],
)
def test_read_surfaces_deny_the_outsider(client, path):
    """越权 (2026-08-16): a logged-in non-member could read a topic's living doc,
    comments and transcript verbatim — only /blocks was guarded, so the UI
    rendered a whole foreign project around one 403. Every read surface must
    answer 403 to the outsider, exactly like /blocks."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login(client, "mallory")
    r = client.get(path.format(tid=tid), headers=_bearer(outsider))
    assert r.status_code == 403, f"{path}: {r.status_code} {r.text[:120]}"


def test_topic_list_denies_the_outsider(client):
    """The project sidebar (titles, activity, participants) is member-only —
    it was readable by ANY logged-in caller holding the project id."""
    pid, _ = _project_topic(client, owner="alice")
    outsider = _login(client, "mallory")
    r = client.get(f"/topics?project_id={pid}", headers=_bearer(outsider))
    assert r.status_code == 403, r.text[:120]


def test_archive_denies_the_outsider(client):
    """Write side of the same hole: a non-member could archive someone else's
    topic."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login(client, "mallory")
    r = client.post(f"/topics/{tid}/archive", json={}, headers=_bearer(outsider))
    assert r.status_code == 403, r.text[:120]


def test_member_still_reads_everything(client):
    """The guard must not lock the door on the people who belong inside."""
    token = _login(client, "alice")
    pid, tid = _project_topic(client, owner="alice")
    for path in (
        f"/topics/{tid}/doc",
        f"/topics/{tid}/comments",
        f"/topics?project_id={pid}",
    ):
        r = client.get(path, headers=_bearer(token))
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:120]}"
