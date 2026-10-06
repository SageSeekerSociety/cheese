"""A person's own projects sit under their own name, not under a team.

Underneath, a person's projects belong to a team with only them in it. That
team does not show: it goes by the person's nickname and avatar, and it holds
no name another team could want.
"""

from __future__ import annotations

from datetime import UTC, datetime

from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.avatars.models import Avatar
from tests.integration.conftest import UserCreator, unique_int


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own(api_client: TestClient, token: str) -> dict:
    resp = api_client.get("/teams/my-teams", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    [own] = [t for t in resp.json()["data"]["teams"] if t["personal"]]
    return own


def _a_picked_avatar(db_session: AsyncSession, portal: BlockingPortal) -> int:
    """Insert a face this person picked, and return its id.

    Handing a profile the id of a row no one wrote is a dangling reference: the
    contract drops it, and the row comes back faceless for the wrong reason.
    Which row holds the seed's *default* face is the seed's business, so don't
    borrow a literal id for a pick either.
    """
    avatar = Avatar(
        url=f"/predefined/avatar_{unique_int(1000, 9999)}.png",
        name="avatar_picked_by_the_person",
        created_at=datetime.now(UTC),
        avatar_type="predefined",
        usage_count=0,
    )

    async def _do() -> int:
        db_session.add(avatar)
        await db_session.flush()
        return avatar.id

    return portal.call(_do)


def test_own_projects_go_under_the_persons_nickname_and_avatar(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
):
    avatar_id = _a_picked_avatar(db_session, _portal)
    user = user_client.create_user(nickname="林夏", avatar_id=avatar_id)
    token = user_client.login(api_client, user.username, user.password)

    own = _own(api_client, token)

    assert (own["name"], own["avatarId"]) == ("林夏", avatar_id)
    resp = api_client.get(f"/teams/{own['id']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["team"]["name"] == "林夏"


def test_a_person_who_never_picked_a_face_gets_no_face_here(
    api_client: TestClient, user_client: UserCreator
):
    """没挑过头像的人，不该在这里领到「全站默认」那一张脸。

    注册会给每份档案写上默认头像的 id；照抄它等于让所有没挑过的人共用一张
    脸。界面要的是「查不到就没有」，由它按本人画彩色首字母。
    """
    user = user_client.create_user(nickname="林夏")
    token = user_client.login(api_client, user.username, user.password)

    own = _own(api_client, token)

    assert own["name"] == "林夏"
    assert own["avatarId"] is None


def test_a_team_may_be_called_what_everyones_own_projects_used_to_be(
    api_client: TestClient, user_client: UserCreator
):
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    _own(api_client, token)

    resp = api_client.post(
        "/teams",
        json={
            "name": "个人",
            "intro": "一支叫这个名字的队",
            "description": "",
            "avatarId": 1,
        },
        headers=_auth(token),
    )

    assert resp.status_code == 201, resp.text
