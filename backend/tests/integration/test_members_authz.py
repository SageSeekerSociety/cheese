"""越权: writing a project's roster over HTTP requires owning or leading it.

Project membership is what lets someone into every topic of the project
(``authorize_topic_access``), so these three writes are the gate that decides
who can read the project at all. Before this was enforced, an anonymous caller
could add itself as a member — or promote itself to lead — on any project.
"""

from app.core.sandbox_auth import mint_scoped_token


def _project(client, owner: str = "alice") -> str:
    body = {"name": "P", "owner_handle": owner}
    return client.post("/projects", json=body).json()["data"]["id"]


def _add(client, pid: str, handle: str, role: str = "member", **kw):
    return client.post(
        f"/projects/{pid}/members", json={"user_handle": handle, "role": role}, **kw
    )


def _handles(client, pid: str) -> list[str]:
    body = client.get(f"/projects/{pid}/members").json()
    return [m["user_handle"] for m in body["data"]["data"]]


def test_owner_can_add_member(client, bearer):
    pid = _project(client)
    r = _add(client, pid, "bob", headers=bearer("alice"))
    assert r.status_code == 200
    assert _handles(client, pid) == ["bob"]


def test_anonymous_caller_cannot_add_member(client):
    """The reported hole: no credential at all used to be enough."""
    pid = _project(client)
    assert _add(client, pid, "mallory").status_code == 403
    assert _handles(client, pid) == []


def test_outsider_with_a_token_cannot_add_member(client, bearer):
    pid = _project(client)
    assert _add(client, pid, "mallory", headers=bearer("mallory")).status_code == 403
    assert _handles(client, pid) == []


def test_plain_member_cannot_add_or_promote(client, bearer):
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer("alice"))

    assert _add(client, pid, "carol", headers=bearer("bob")).status_code == 403
    r = client.put(
        f"/projects/{pid}/members/bob",
        json={"role": "lead"},
        headers=bearer("bob"),
    )
    assert r.status_code == 403
    # 自我提权 must not have happened.
    members = client.get(f"/projects/{pid}/members").json()["data"]["data"]
    assert members[0]["role"] == "member"


def test_lead_can_manage_the_roster(client, bearer):
    """A lead is a manager too — ``projects.owner_handle`` is nullable, so leads
    are the only thing keeping an owner-less project manageable."""
    pid = _project(client)
    _seed_member(client, pid, "bob", "lead")
    _clear_owner(client, pid)  # nobody owns it — the lead is all that is left

    assert _add(client, pid, "carol", headers=bearer("bob")).status_code == 200
    r = client.put(
        f"/projects/{pid}/members/carol",
        json={"role": "mentor"},
        headers=bearer("bob"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "mentor"
    r = client.delete(f"/projects/{pid}/members/carol", headers=bearer("bob"))
    assert r.status_code == 200
    assert _handles(client, pid) == ["bob"]


def test_token_wins_over_a_forged_body_handle(client, bearer):
    """This surface reads the actor from the verified token only — a body field
    naming the owner is ignored, in both directions."""
    pid = _project(client)

    forged = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "mallory", "role": "lead", "actor": "alice"},
        headers=bearer("mallory"),
    )
    assert forged.status_code == 403
    assert _handles(client, pid) == []

    real = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "bob", "actor": "mallory"},
        headers=bearer("alice"),
    )
    assert real.status_code == 200


def test_agent_scoped_token_cannot_write_the_roster(client):
    """芝士 holding a valid per-turn token for THIS project is still refused —
    promoting a member to lead through this route is the exploit that surfaced
    the missing check."""
    pid = _project(client)
    _seed_member(client, pid, "bob", "member")
    scoped = {"X-Cheese-Token": mint_scoped_token(project_id=pid)}

    assert _add(client, pid, "carol", headers=scoped).status_code == 403
    r = client.put(
        f"/projects/{pid}/members/bob", json={"role": "lead"}, headers=scoped
    )
    assert r.status_code == 403
    r = client.delete(f"/projects/{pid}/members/bob", headers=scoped)
    assert r.status_code == 403


def test_update_and_delete_are_guarded_too(client, bearer):
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer("alice"))

    assert (
        client.put(
            f"/projects/{pid}/members/bob",
            json={"role": "lead"},
            headers=bearer("mallory"),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/projects/{pid}/members/bob", headers=bearer("mallory")
        ).status_code
        == 403
    )
    assert client.delete(f"/projects/{pid}/members/bob").status_code == 403
    assert _handles(client, pid) == ["bob"]


def test_reading_the_roster_stays_open(client, bearer):
    """Only the writes were closed: the UI reads this list without a token."""
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer("alice"))
    assert client.get(f"/projects/{pid}/members").status_code == 200


def _seed_member(client, pid: str, handle: str, role: str) -> None:
    """Write a roster row straight to the repository (no HTTP, no actor check).

    Setup only: a project that has a lead but no owner can no longer be built
    through the API — which is exactly what the guard is for.
    """
    import asyncio
    import uuid

    from app.domain.membership.repositories import MemberRepository
    from app.domain.project.models import ProjectRole

    async def _insert() -> None:
        async with client.test_factory() as s:  # type: ignore[attr-defined]
            await MemberRepository(s).add(
                project_id=uuid.UUID(pid),
                user_handle=handle,
                role=ProjectRole(role),
            )
            await s.commit()

    asyncio.run(_insert())


def _clear_owner(client, pid: str) -> None:
    """Make the project owner-less — the state this project is actually in
    (``projects.owner_handle`` is NULL), tracked separately as its own bug."""
    import asyncio
    import uuid

    from app.domain.project.repositories import ProjectRepository

    async def _update() -> None:
        async with client.test_factory() as s:  # type: ignore[attr-defined]
            project = await ProjectRepository(s).get(uuid.UUID(pid))
            assert project is not None
            project.owner_handle = None
            await s.commit()

    asyncio.run(_update())
