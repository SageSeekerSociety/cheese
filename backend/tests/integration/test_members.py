"""Project membership CRUD over HTTP.

Every write here goes out as the project OWNER: the three write routes are
authorized against a verified token (see test_members_authz.py), so a call
without one is a 403 rather than a CRUD result.
"""

OWNER = "owner-1"
MISSING_PROJECT = "00000000-0000-0000-0000-000000000000"


def _create_project(client, name: str = "Demo") -> str:
    r = client.post("/projects", json={"name": name, "owner_handle": OWNER})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_add_and_list_members(client, bearer):
    project_id = _create_project(client)

    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice", "role": "lead"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["user_handle"] == "alice"
    assert body["data"]["role"] == "lead"
    assert body["data"]["project_id"] == project_id

    # role defaults to member when omitted
    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "bob"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "member"

    r = client.get(f"/projects/{project_id}/members")
    body = r.json()
    # 名册 = 加进来的人 + 项目所有者（他不是成员表里的一行，见
    # test_new_project_roster.py），所有者排在最前面。
    assert body["data"]["total"] == 3
    handles = [m["user_handle"] for m in body["data"]["data"]]
    assert handles == [OWNER, "alice", "bob"]


def test_duplicate_member_rejected(client, bearer):
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )
    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 422


def test_update_member_role(client, bearer):
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice", "role": "member"},
        headers=bearer(OWNER),
    )

    r = client.put(
        f"/projects/{project_id}/members/alice",
        json={"role": "mentor"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "mentor"

    rows = {
        m["user_handle"]: m
        for m in client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    }
    assert rows["alice"]["role"] == "mentor"


def test_update_missing_member_404(client, bearer):
    project_id = _create_project(client)
    r = client.put(
        f"/projects/{project_id}/members/ghost",
        json={"role": "lead"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 404


def test_delete_member(client, bearer):
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )

    r = client.delete(f"/projects/{project_id}/members/alice", headers=bearer(OWNER))
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True

    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    # 移出的是 alice；所有者留在名册上——移出成员碰不到他，他不在那张表里。
    assert [m["user_handle"] for m in rows] == [OWNER]


def test_delete_missing_member_404(client, bearer):
    project_id = _create_project(client)
    r = client.delete(f"/projects/{project_id}/members/ghost", headers=bearer(OWNER))
    assert r.status_code == 404


def test_endpoints_require_existing_project(client, bearer):
    """A missing project reads as 404 for everyone — the existence check runs
    before the authorization one, so this stays a 404 rather than a 403."""
    r = client.post(
        f"/projects/{MISSING_PROJECT}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 404

    r = client.get(f"/projects/{MISSING_PROJECT}/members")
    assert r.status_code == 404

    r = client.put(
        f"/projects/{MISSING_PROJECT}/members/alice",
        json={"role": "lead"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 404

    r = client.delete(
        f"/projects/{MISSING_PROJECT}/members/alice", headers=bearer(OWNER)
    )
    assert r.status_code == 404


def test_roster_carries_nickname_and_avatar(client, bearer):
    """聊天面板拿名册渲染作者的名字和头像，所以列表必须带上这两样。

    消息里存的 author 是登录 handle（后端有意固定成这个防伪造），前端只能靠这个
    名册把 handle 换成昵称、把 avatar_id 换成头像图。名册背后没有 fusion 用户档案
    的 handle（机器人、还没注册的人）拿到的是 avatar_id=null —— 前端据此退回彩色
    首字母，而不是给陌生人配一张默认脸。
    """
    import asyncio
    from datetime import UTC, datetime

    from app.domain.user.models import User, UserProfile

    async def _seed() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            u = User(
                username="alice",
                email="alice@example.com",
                created_at=now,
                updated_at=now,
            )
            s.add(u)
            await s.flush()
            s.add(
                UserProfile(
                    user_id=u.id,
                    nickname="爱丽丝",
                    intro="",
                    avatar_id=4242,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

    asyncio.run(_seed())

    project_id = _create_project(client)
    for handle in ("alice", "nobody"):
        assert (
            client.post(
                f"/projects/{project_id}/members",
                json={"user_handle": handle},
                headers=bearer(OWNER),
            ).status_code
            == 200
        )

    rows = {
        m["user_handle"]: m
        for m in client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    }
    assert rows["alice"]["name"] == "爱丽丝"
    assert rows["alice"]["avatar_id"] == 4242
    # 名册上有、但背后没有用户档案：名字退回 handle，头像为 null。
    assert rows["nobody"]["name"] == "nobody"
    assert rows["nobody"]["avatar_id"] is None


def test_roster_reports_the_global_default_avatar_as_no_avatar(client, bearer):
    """指向全局默认头像的成员，名册要报 avatar_id=null。

    注册的每条路径都写死 ``default_avatar_id=1``，所以「档案指向 default 类型的
    头像」意味着这个人从来没设过头像，不是他选了这张脸。照原样报出去，所有没设过
    头像的人在聊天面板里会共用同一张脸 —— 比按 handle 哈希、每人一色的彩色首字母
    更难分辨谁是谁，而认人正是头像在聊天面板里唯一的用处。自己挑的 predefined 和
    自己传的 upload 照常报原 id。
    """
    import asyncio
    from datetime import UTC, datetime

    from app.domain.avatars.models import Avatar
    from app.domain.user.models import User, UserProfile

    picks: dict[str, str] = {
        "default_dan": "default",
        "predefined_pat": "predefined",
        "upload_uma": "upload",
    }
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

    project_id = _create_project(client)
    for handle in picks:
        assert (
            client.post(
                f"/projects/{project_id}/members",
                json={"user_handle": handle},
                headers=bearer(OWNER),
            ).status_code
            == 200
        )

    rows = {
        m["user_handle"]: m
        for m in client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    }
    # 没设过头像 → null，前端退回彩色首字母。
    assert rows["default_dan"]["avatar_id"] is None
    # 自己挑的 / 自己传的 → 照常给出图片 id。
    assert rows["predefined_pat"]["avatar_id"] == avatar_ids["predefined_pat"]
    assert rows["upload_uma"]["avatar_id"] == avatar_ids["upload_uma"]
    # 名字不受影响：三个人都还是自己的昵称。
    assert rows["default_dan"]["name"] == "DEFAULT_DAN"
