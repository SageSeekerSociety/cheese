"""Who is in a channel, and its managers adding and removing people, over HTTP."""

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

MISSING_TOPIC = "00000000-0000-0000-0000-000000000000"


def _agent(client, tid: str) -> str:
    """The handle the agent seated in this room acts under.

    Read from the roster rather than derived from the room: a room is a
    collaboration space and does not name an agent, so which agent sits here is
    a fact to look up.
    """
    seats = [m for m in _roster(client, tid) if m["agent"]]
    assert len(seats) == 1, seats
    return seats[0]["member_handle"]


def _topic(client, created_by: str = "alice") -> str:
    """A room in a project whose team has bob and carol on it — a room seats
    only people who are in the project."""
    p = post_project(client, json={"name": "P"}, owner=created_by).json()["data"]
    for handle in {"bob", "carol"} - {created_by}:
        join_project_team(client, p["id"], handle)
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers(created_by),
    ).json()["data"]
    return t["id"]


def _roster(client, tid: str) -> list[dict]:
    return client.get(f"/topics/{tid}/members").json()["data"]["data"]


def test_seed_creator_owner_and_cheese_member(client):
    """A newborn topic seeds its roster: creator = owner, 芝士 = member.

    芝士 joins as THIS topic's own 分身 rather than the shared platform account,
    so the seat identifies one 分身 and can be taken from it alone."""
    tid = _topic(client, created_by="alice")
    members = {m["member_handle"]: m for m in _roster(client, tid)}
    assert members["alice"]["role"] == "owner"
    assert members[_agent(client, tid)]["role"] == "member"
    assert members[_agent(client, tid)]["agent"] is True
    assert members[_agent(client, tid)]["name"] == "芝士"
    assert members["alice"]["agent"] is False


def test_owner_can_add_member(client):
    tid = _topic(client, created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["member_handle"] == "bob"
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert handles == {"alice", _agent(client, tid), "bob"}


def test_a_person_outside_the_project_is_not_seated(client):
    """Seating someone the project does not have would let them in without an
    invitation they accepted: people come into the project first."""
    tid = _topic(client, created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "stranger", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert "stranger" not in {m["member_handle"] for m in _roster(client, tid)}


def test_non_manager_cannot_add_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    # bob is a plain member → may not add anyone.
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol", "actor": "bob"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 403


def test_duplicate_member_rejected(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_remove_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    r = client.delete(
        f"/topics/{tid}/members/bob", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert "bob" not in handles


def test_endpoints_require_existing_topic(client):
    r = client.get(f"/topics/{MISSING_TOPIC}/members")
    assert r.status_code == 404
    r = client.post(
        f"/topics/{MISSING_TOPIC}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 404


def test_each_agent_row_is_named_after_the_agent_seated_there(client):
    """名册上每个 AI 队友一行，各写各的名字。

    界面上「这一行的 AI 队友叫什么」只有这一个来源——对话里它说的每一句话、名册上
    它那一行、头像上那个字，读的都是这里。座位账号自己的昵称是建号那一刻写死的
    常量，照原样报出去，两个队友就成了同一个名字。
    """
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    tid = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    seat = _agent(client, tid)
    assert {m["member_handle"]: m["name"] for m in _roster(client, tid)}[seat] == "芝士"

    reviewer = client.post(
        f"/projects/{p['id']}/agents",
        json={"handle": "reviewer", "display_name": "评审"},
    ).json()["data"]
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": reviewer["seat_handle"], "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    rows = {m["member_handle"]: m for m in _roster(client, tid) if m["agent"]}
    assert rows[seat]["name"] == "芝士"
    assert rows[reviewer["seat_handle"]]["name"] == "评审"


def test_roster_reports_the_global_default_avatar_as_no_avatar(client):
    """名册要区分「挑过头像」和「从来没挑过」，后者报 avatar_id=null。

    注册的每条路径都写死 ``default_avatar_id=1``，所以「档案上有个头像 id」并不
    意味着这个人挑过头像。照原样报出去，所有没挑过的人在界面上共用同一张脸——比
    按 handle 哈希、每人一色的彩色首字母更难认出谁是谁，而认人正是头像的全部职责。
    """
    import asyncio
    from datetime import UTC, datetime

    from app.domain.avatars.models import Avatar
    from app.domain.user.models import User, UserProfile

    picks = {"dan": "default", "pat": "predefined", "uma": "upload"}
    avatar_ids: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            for handle, avatar_type in picks.items():
                avatar = Avatar(
                    url="",
                    name=f"{avatar_type}.png",
                    avatar_type=avatar_type,
                    created_at=now,
                    usage_count=0,
                )
                s.add(avatar)
                await s.flush()
                avatar_ids[handle] = avatar.id
                u = User(
                    username=handle,
                    email=f"{handle}@example.com",
                    created_at=now,
                    updated_at=now,
                )
                s.add(u)
                await s.flush()
                s.add(
                    UserProfile(
                        user_id=u.id,
                        nickname=handle.upper(),
                        intro="",
                        avatar_id=avatar.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await s.commit()

    asyncio.run(_seed())

    tid = _topic(client, created_by="dan")
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    for handle in ("pat", "uma"):
        join_project_team(client, pid, handle)
        assert (
            client.post(
                f"/topics/{tid}/members",
                json={"handle": handle, "actor": "dan"},
                headers=session_auth_headers("dan"),
            ).status_code
            == 200
        )

    rows = {m["member_handle"]: m for m in _roster(client, tid)}
    # 没挑过 → null，界面退回彩色首字母。
    assert rows["dan"]["avatar_id"] is None
    # 自己挑的 / 自己传的 → 照常给出图片 id。
    assert rows["pat"]["avatar_id"] == avatar_ids["pat"]
    assert rows["uma"]["avatar_id"] == avatar_ids["uma"]
    # 名册上有、但背后没有用户档案的 handle（芝士的座位就是）也是 null。
    assert rows[_agent(client, tid)]["avatar_id"] is None
    # 名字不受影响。
    assert rows["dan"]["name"] == "DAN"
