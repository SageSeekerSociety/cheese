"""Topic membership (话题成员名册) CRUD + permissions over HTTP."""

import uuid

from app.domain.identity.handles import topic_agent_handle

MISSING_TOPIC = "00000000-0000-0000-0000-000000000000"


def _agent(tid: str) -> str:
    """The handle THIS topic's 分身 sits in the roster under (分身独立身份)."""
    return topic_agent_handle(uuid.UUID(tid))


def _topic(client, created_by: str = "alice") -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": created_by},
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
    assert members[_agent(tid)]["role"] == "member"
    assert members[_agent(tid)]["agent"] is True
    assert members[_agent(tid)]["name"] == "芝士"
    assert members["alice"]["agent"] is False


def test_owner_can_add_member(client):
    tid = _topic(client, created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["member_handle"] == "bob"
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert handles == {"alice", _agent(tid), "bob"}


def test_non_manager_cannot_add_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    # bob is a plain member → may not add anyone.
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol", "role": "member", "actor": "bob"},
    )
    assert r.status_code == 403


def test_admin_can_manage_but_stranger_cannot(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "admin", "actor": "alice"},
    )
    # admin bob adds carol
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol", "role": "member", "actor": "bob"},
    )
    assert r.status_code == 200
    # a non-member stranger cannot
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "dave", "role": "member", "actor": "stranger"},
    )
    assert r.status_code == 403


def test_duplicate_member_rejected(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    assert r.status_code == 422


def test_update_role(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    r = client.put(
        f"/topics/{tid}/members/bob",
        json={"role": "admin", "actor": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "admin"


def test_non_manager_cannot_update_role(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    r = client.put(
        f"/topics/{tid}/members/cheese",
        json={"role": "admin", "actor": "bob"},
    )
    assert r.status_code == 403


def test_cannot_remove_last_owner(client):
    tid = _topic(client, created_by="alice")
    r = client.delete(f"/topics/{tid}/members/alice?actor=alice")
    assert r.status_code == 422


def test_cannot_demote_last_owner(client):
    tid = _topic(client, created_by="alice")
    r = client.put(
        f"/topics/{tid}/members/alice",
        json={"role": "member", "actor": "alice"},
    )
    assert r.status_code == 422


def test_remove_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    r = client.delete(f"/topics/{tid}/members/bob?actor=alice")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert "bob" not in handles


def test_second_owner_lets_first_be_removed(client):
    """The last-owner guard is about the COUNT — promote a second owner and the
    first can leave."""
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "owner", "actor": "alice"},
    )
    r = client.delete(f"/topics/{tid}/members/alice?actor=bob")
    assert r.status_code == 200


def test_endpoints_require_existing_topic(client):
    r = client.get(f"/topics/{MISSING_TOPIC}/members")
    assert r.status_code == 404
    r = client.post(
        f"/topics/{MISSING_TOPIC}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    assert r.status_code == 404


def test_agent_row_is_named_after_the_agent_the_room_was_handed_to(client):
    """换了 AI 队友，名册上那一行就得跟着改名。

    界面上「这个房间的 AI 队友叫什么」只有这一个来源——对话里它说的每一句话、
    名册上它那一行、头像上那个字，读的都是这里。而座位账号自己的昵称是建号那一刻
    写死的常量（``IdentityService.ensure_topic_agent_user``），换人格不会动它，
    所以照原样报出去，换完队友屏幕上留着的仍是上一个的名字——和「换人根本没生效」
    长得一模一样。
    """
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    tid = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "alice"},
    ).json()["data"]["id"]

    seat = _agent(tid)
    assert {m["member_handle"]: m["name"] for m in _roster(client, tid)}[seat] == "芝士"

    reviewer = client.post(
        f"/projects/{p['id']}/agents",
        json={"handle": "reviewer", "display_name": "评审"},
    ).json()["data"]
    assert (
        client.put(
            f"/topics/{tid}/agent", json={"instance_id": reviewer["id"]}
        ).status_code
        == 200
    )

    row = {m["member_handle"]: m for m in _roster(client, tid)}[seat]
    assert row["name"] == "评审"
    # 座位没换，只是它现在归另一个队友：换人不能把这个房间的 AI 身份换掉。
    assert row["agent"] is True
    assert row["member_handle"] == seat


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
    for handle in ("pat", "uma"):
        assert (
            client.post(
                f"/topics/{tid}/members",
                json={"handle": handle, "role": "member", "actor": "dan"},
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
    assert rows[_agent(tid)]["avatar_id"] is None
    # 名字不受影响。
    assert rows["dan"]["name"] == "DAN"
