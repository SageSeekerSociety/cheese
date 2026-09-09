"""POST /topics/{id}/split — real actor resolution + access control (跟 split
端点鉴权缺失 fix 配套): the endpoint used to trust `body.created_by` outright and
never checked the caller had access to the room, so anyone could dispatch work
in anyone else's room under an arbitrary owner.

The ownership question survives the move from a room-per-task to a thread; only
the shape of the answer changed. A room answers "who does this belong to" with a
roster, and the bug users hit was a 分身-initiated split seeding that roster from
`owner_handle="cheese"` (which `seed()` skips) and leaving every human off it. A
thread answers with `owner_handle` and has no roster at all — 唯一的主 is the
whole difference — so that is what these assert on now."""

from tests.integration.conftest import session_token


def _login(handle: str) -> str:
    return session_token(handle)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project_topic(client, owner: str) -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _members(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/members").json()["data"]["data"]


def test_split_outsider_token_denied(client):
    """越权: a token-authenticated caller who is neither a topic member nor a
    project member cannot split someone else's topic."""
    _, tid = _project_topic(client, owner="alice")
    outsider = _login("mallory")
    r = client.post(
        f"/topics/{tid}/split",
        json=dict(
            reviewer_handle="alice", **{"title": "偷偷拆一个", "created_by": "mallory"}
        ),
        headers=_bearer(outsider),
    )
    assert r.status_code == 403


def test_split_owner_allowed_and_the_work_belongs_to_them(client):
    """The room's owner may dispatch work in it, and the work is theirs."""
    _, tid = _project_topic(client, owner="alice")
    token = _login("alice")
    r = client.post(
        f"/topics/{tid}/split",
        json=dict(reviewer_handle="alice", **{"title": "子任务"}),
        headers=_bearer(token),
    )
    assert r.status_code == 200
    task = r.json()["data"]

    assert task["owner_handle"] == "alice"
    assert task["room_id"] == tid


def test_a_thread_does_not_get_a_roster_of_its_own(client):
    """唯一的主 is the difference between work and the room it happens in.

    A room's roster is what a room is for; copying it onto every piece of work
    was one of the costs work paid for being a room, and dropping it is most of
    why work stopped being one. The room's own roster is untouched.
    """
    _, tid = _project_topic(client, owner="alice")
    before = {m["member_handle"] for m in _members(client, tid)}

    task = client.post(
        f"/topics/{tid}/split",
        json=dict(reviewer_handle="alice", **{"title": "子任务"}),
    ).json()["data"]

    assert task["owner_handle"] is not None
    # The thread is not a topic, so there is nothing with a roster to ask about.
    assert client.get(f"/topics/{task['id']}/members").status_code == 404
    assert {m["member_handle"] for m in _members(client, tid)} == before


def test_split_ignores_forged_created_by_in_body(client):
    """A verified token wins over a forged `created_by` in the body — the child
    roster owner is the REAL caller, not whoever the body claims."""
    _, tid = _project_topic(client, owner="alice")
    token = _login("alice")
    r = client.post(
        f"/topics/{tid}/split",
        json=dict(
            reviewer_handle="alice",
            **{"title": "子任务", "created_by": "mallory-forged"},
        ),
        headers=_bearer(token),
    )
    assert r.status_code == 200
    task = r.json()["data"]

    assert task["owner_handle"] == "alice"


def test_split_by_cheese_agent_defaults_owner_to_parent_owner(client):
    """The bug users actually hit: a 分身-initiated split (no human token, the
    `cheese` agent as `created_by` — same shape `cheese split` sends) used to
    seed the child roster from `owner_handle="cheese"` alone. `seed()` skips
    "cheese" as owner, so the child ended up belonging to NOBODY — and the
    accept card it ends in had no one to land on. It must default to the room's
    real human owner."""
    _, tid = _project_topic(client, owner="alice")
    r = client.post(
        f"/topics/{tid}/split",
        json=dict(
            reviewer_handle="alice",
            **{"title": "分身拆出的子任务", "created_by": "cheese"},
        ),
    )
    assert r.status_code == 200

    assert r.json()["data"]["owner_handle"] == "alice"


def test_split_with_no_identified_human_still_gets_parent_owner(client):
    """Even a bare Phase-0 call with no `created_by` at all (no token, no body
    field) must not leave the work ownerless."""
    _, tid = _project_topic(client, owner="alice")
    r = client.post(
        f"/topics/{tid}/split",
        json=dict(reviewer_handle="alice", **{"title": "无发起人拆分"}),
    )
    assert r.status_code == 200

    assert r.json()["data"]["owner_handle"] == "alice"


def test_project_member_can_split_even_if_not_on_topic_roster(client):
    """权限属于项目: a project member may split the project's topics even
    without being on that specific topic's roster (same rule as doc edits)."""
    import asyncio
    import uuid

    from app.domain.identity.actor import Actor
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
                # Roster writes are authorized — seed as the project owner.
                actor=Actor(handle="alice", user_id=None, is_agent=False, via="token"),
            )
            await s.commit()

    asyncio.run(_add_member())

    r = client.post(
        f"/topics/{tid}/split",
        json=dict(reviewer_handle="alice", **{"title": "bob 拆的子任务"}),
        headers=_bearer(token),
    )
    assert r.status_code == 200
