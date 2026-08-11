"""POST /topics/{id}/split — real actor resolution + access control (跟 split
端点鉴权缺失 fix 配套): the endpoint used to trust `body.created_by` outright and
never checked the caller had access to the PARENT topic, so anyone could split
anyone else's topic under an arbitrary roster owner. It also only ever seeded
the child's roster from the requested owner — a 分身-initiated split (owner
handle "cheese", which `seed()` deliberately skips) left every human silently
off the new sub-topic's member list, which is what users actually noticed."""

from tests.integration.conftest import session_token


def _login(handle: str) -> str:
    return session_token(handle)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = client.post("/api/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _members(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/members").json()["data"]["data"]


def test_split_outsider_token_denied(client):
    """越权: a token-authenticated caller who is neither a topic member nor a
    project member cannot split someone else's topic."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login("mallory")
    r = client.post(
        f"/api/topics/{tid}/split",
        json={"title": "偷偷拆一个", "created_by": "mallory"},
        headers=_bearer(outsider),
    )
    assert r.status_code == 403


def test_split_owner_allowed_and_child_roster_has_owner(client):
    """The parent topic's owner may split it, and becomes the child's owner."""
    _, tid = _project_topic(client, owner="alice")
    token = _login("alice")
    r = client.post(
        f"/api/topics/{tid}/split",
        json={"title": "子任务"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    sub = r.json()["data"]

    handles = {m["member_handle"]: m["role"] for m in _members(client, sub["id"])}
    assert handles.get("alice") == "owner"


def test_split_ignores_forged_created_by_in_body(client):
    """A verified token wins over a forged `created_by` in the body — the child
    roster owner is the REAL caller, not whoever the body claims."""
    _, tid = _project_topic(client, owner="alice")
    token = _login("alice")
    r = client.post(
        f"/api/topics/{tid}/split",
        json={"title": "子任务", "created_by": "mallory-forged"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    sub = r.json()["data"]

    handles = {m["member_handle"] for m in _members(client, sub["id"])}
    assert "alice" in handles
    assert "mallory-forged" not in handles


def test_split_by_cheese_agent_defaults_owner_to_parent_owner(client):
    """The bug users actually hit: a 分身-initiated split (no human token, the
    `cheese` agent as `created_by` — same shape `cheese split` sends) used to
    seed the child roster from `owner_handle="cheese"` alone. `seed()` skips
    "cheese" as owner, so the child ended up with NO owner/admin at all —
    nobody could manage its member list. It must now default to the parent's
    real human owner, who becomes the child's owner too (not just a member)."""
    _, tid = _project_topic(client, owner="alice")
    r = client.post(
        f"/api/topics/{tid}/split",
        json={"title": "分身拆出的子任务", "created_by": "cheese"},
    )
    assert r.status_code == 200
    sub = r.json()["data"]

    handles = {m["member_handle"]: m["role"] for m in _members(client, sub["id"])}
    assert handles.get("alice") == "owner"


def test_split_with_no_identified_human_still_gets_parent_owner(client):
    """Even a bare Phase-0 call with no `created_by` at all (no token, no body
    field) must not leave the child ownerless."""
    _, tid = _project_topic(client, owner="alice")
    r = client.post(f"/api/topics/{tid}/split", json={"title": "无发起人拆分"})
    assert r.status_code == 200
    sub = r.json()["data"]

    handles = {m["member_handle"]: m["role"] for m in _members(client, sub["id"])}
    assert handles.get("alice") == "owner"


def test_project_member_can_split_even_if_not_on_topic_roster(client):
    """权限属于项目: a project member may split the project's topics even
    without being on that specific topic's roster (same rule as doc edits)."""
    import asyncio
    import uuid

    from app.domain.membership.services import MemberService
    from app.domain.project.models import ProjectRole

    pid, tid = _project_topic(client, owner="alice")
    token = _login("bob")

    async def _add_member() -> None:
        async with client.test_factory() as s:  # type: ignore[attr-defined]
            await MemberService(s).add(
                project_id=uuid.UUID(pid),
                user_handle="bob",
                role=ProjectRole.member,
            )
            await s.commit()

    asyncio.run(_add_member())

    r = client.post(
        f"/api/topics/{tid}/split",
        json={"title": "bob 拆的子任务"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
