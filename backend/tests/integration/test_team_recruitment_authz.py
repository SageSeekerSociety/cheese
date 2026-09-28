"""越权回归：团队招募列表从前不看那支队伍收不收得着。

``backend/app/api/routes/recruitment.py`` 的 ``list_team_recruitment_posts``
（``GET /teams/{teamId}/recruitment``）从前既没有 ``require_auth_user``，也从不
过团队可见性门 ``TeamService.visible_team``：它直接把路径上的 ``teamId`` 交给
``RecruitmentService.list_by_team`` → ``RecruitmentRepository.list_by_team``，
后者只按 ``team_id + deleted_at is null`` 过滤。

于是任何人不需要登录，遍历小整数 ``teamId`` 就能读到**任意团队**（含
``visibility=stealth`` 的队）**任意状态**的招募帖正文与联系方式，以及团队身份。
产品口径在 ``backend/app/domain/team/models.py:35-42``：stealth 队只能经加入链接
抵达。这与 #1923 讨论板八个 handler 「只核登录、不核父对象可见性」同一形状。

这份用例把那条路径钉死，判据是 ``GET /teams/{teamId}`` 正在用的那一条
（``TeamService.visible_team``）：成员看得见自己的队，公开共享队谁都看得见，
看不见的队答 404 —— 招募列表与团队本身逐字同答，不确认它存在。

有意不动的一条：``list_by_team`` 的 docstring 写着 "any status"，帖子的可见性与
帖子状态无关 —— 本用例不断言状态过滤，团队里的 CLOSED 帖对能看到该队的人依旧列出。
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


def _registered(user_client: UserCreator, api_client: TestClient):
    """一个真的注册过、也登录了的用户，带着它的 token。"""
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _bearer(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


def _create_team(
    api_client: TestClient, owner, *, visibility: str | None = None
) -> int:
    suffix = unique_int()
    resp = api_client.post(
        "/teams",
        json={
            "name": f"Recruit Authz Team ({suffix})",
            "intro": "Test",
            "description": "A team for recruitment authz tests",
            "avatarId": 1,
        },
        headers=_bearer(owner),
    )
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["data"]["team"]["id"]
    if visibility is not None:
        patched = api_client.patch(
            f"/teams/{team_id}",
            json={"visibility": visibility},
            headers=_bearer(owner),
        )
        assert patched.status_code == 200, patched.text
    return team_id


def _create_post(
    api_client: TestClient,
    owner,
    team_id: int,
    *,
    title: str,
    content: str,
    contact: str | None = None,
) -> dict:
    resp = api_client.post(
        f"/teams/{team_id}/recruitment",
        json={"title": title, "content": content, "contact": contact},
        headers=_bearer(owner),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["post"]


def _add_member(api_client: TestClient, owner, team_id: int, member) -> None:
    """邀请并接受 —— 一条真的进队的路（不是直接写库）。"""
    inv = api_client.post(
        f"/teams/{team_id}/invitations",
        json={"userId": member.user_id, "role": "MEMBER"},
        headers=_bearer(owner),
    )
    assert inv.status_code == 201, inv.text
    inv_id = inv.json()["data"]["invitation"]["id"]
    accepted = api_client.post(
        f"/users/me/team-invitations/{inv_id}/accept",
        headers=_bearer(member),
    )
    assert accepted.status_code == 204, accepted.text


class TestTeamRecruitmentIsBehindTheTeamGate:
    @pytest.fixture
    def stealth(self, user_client: UserCreator, api_client: TestClient) -> dict:
        """一支隐身队，一条带联系方式与正文的招募帖；outsider 与它无关。"""
        owner = _registered(user_client, api_client)
        outsider = _registered(user_client, api_client)
        member = _registered(user_client, api_client)

        team_id = _create_team(api_client, owner, visibility="stealth")
        marker = unique_int()
        post = _create_post(
            api_client,
            owner,
            team_id,
            title=f"秘密招募 {marker}",
            content=f"正文 {marker}",
            contact=f"secret-{marker}@example.com",
        )
        return {
            "owner": owner,
            "outsider": outsider,
            "member": member,
            "team_id": team_id,
            "post": post,
            "marker": marker,
        }

    def test_anonymous_cannot_list_team_recruitment(
        self, api_client: TestClient, stealth: dict
    ) -> None:
        """不登录就这一条路都不通 —— 从前它答 200 并吐帖子。"""
        resp = api_client.get(f"/teams/{stealth['team_id']}/recruitment")
        assert resp.status_code == 401, f"{resp.status_code} {resp.text}"
        assert str(stealth["marker"]) not in resp.text

    def test_outsider_gets_the_same_answer_as_the_team_itself(
        self, api_client: TestClient, stealth: dict
    ) -> None:
        """隐身队：团队本身与它的招募列表答同一个 404，正文与联系方式不外泄。"""
        team_resp = api_client.get(
            f"/teams/{stealth['team_id']}", headers=_bearer(stealth["outsider"])
        )
        posts_resp = api_client.get(
            f"/teams/{stealth['team_id']}/recruitment",
            headers=_bearer(stealth["outsider"]),
        )
        assert posts_resp.status_code == team_resp.status_code == 404, (
            f"team={team_resp.status_code} posts={posts_resp.status_code}"
        )
        assert str(stealth["marker"]) not in posts_resp.text
        assert "secret-" not in posts_resp.text

    def test_member_reads_their_stealth_team(
        self, api_client: TestClient, stealth: dict
    ) -> None:
        """队内成员照旧看得见自己队的招募帖。"""
        _add_member(
            api_client, stealth["owner"], stealth["team_id"], stealth["member"]
        )

        resp = api_client.get(
            f"/teams/{stealth['team_id']}/recruitment",
            headers=_bearer(stealth["member"]),
        )
        assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
        posts = resp.json()["data"]["posts"]
        assert any(p["id"] == stealth["post"]["id"] for p in posts), posts

    def test_owner_still_reads_their_own_stealth_team(
        self, api_client: TestClient, stealth: dict
    ) -> None:
        resp = api_client.get(
            f"/teams/{stealth['team_id']}/recruitment",
            headers=_bearer(stealth["owner"]),
        )
        assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
        assert str(stealth["marker"]) in resp.text


class TestTheVisibilityIsTheTeamsOwnRule:
    """公开共享队谁都读得到；判据就是 ``GET /teams/{teamId}`` 的那一条。"""

    def test_outsider_reads_a_public_team_recruitment(
        self, user_client: UserCreator, api_client: TestClient
    ) -> None:
        owner = _registered(user_client, api_client)
        outsider = _registered(user_client, api_client)
        team_id = _create_team(api_client, owner)  # 默认 public
        marker = unique_int()
        post = _create_post(
            api_client, owner, team_id, title=f"公开招募 {marker}", content="欢迎加入"
        )

        team_resp = api_client.get(f"/teams/{team_id}", headers=_bearer(outsider))
        posts_resp = api_client.get(
            f"/teams/{team_id}/recruitment", headers=_bearer(outsider)
        )
        assert team_resp.status_code == 200, team_resp.text
        assert posts_resp.status_code == 200, (
            f"{posts_resp.status_code} {posts_resp.text}"
        )
        posts = posts_resp.json()["data"]["posts"]
        assert any(p["id"] == post["id"] for p in posts), posts

    def test_a_stranger_is_not_confirmed_a_stealth_team_exists(
        self, user_client: UserCreator, api_client: TestClient
    ) -> None:
        """隐身队与压根不存在的队，外人得到的答案逐字相同。"""
        owner = _registered(user_client, api_client)
        stranger = _registered(user_client, api_client)
        team_id = _create_team(api_client, owner, visibility="stealth")
        _create_post(api_client, owner, team_id, title="秘密", content="秘密")

        hidden = api_client.get(
            f"/teams/{team_id}/recruitment", headers=_bearer(stranger)
        )
        missing = api_client.get(
            f"/teams/{team_id + 100000000}/recruitment", headers=_bearer(stranger)
        )
        assert hidden.status_code == missing.status_code == 404, (
            f"hidden={hidden.status_code} missing={missing.status_code}"
        )


class TestThePlazaIsNotTightened:
    """公开广场（``GET /recruitment``）行为不变：匿名可读，且仍只列 OPEN 帖。

    这次改的是团队作用域的那一条路；广场的口径（对所有人公开、只列 OPEN）是既有
    设计，本用例把它钉住，免得这次顺手动它。
    """

    def test_the_plaza_is_still_open_and_lists_open_posts(
        self, user_client: UserCreator, api_client: TestClient
    ) -> None:
        owner = _registered(user_client, api_client)
        team_id = _create_team(api_client, owner)
        marker = unique_int()
        open_post = _create_post(
            api_client, owner, team_id, title=f"广场帖 {marker}", content="OPEN 帖"
        )

        closed_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": f"关闭帖 {marker}", "content": "CLOSED 帖"},
            headers=_bearer(owner),
        )
        closed_id = closed_resp.json()["data"]["post"]["id"]
        patched = api_client.patch(
            f"/recruitment/{closed_id}",
            json={"status": "CLOSED"},
            headers=_bearer(owner),
        )
        assert patched.status_code == 200, patched.text

        resp = api_client.get("/recruitment")  # 匿名
        assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
        ids = [p["id"] for p in resp.json()["data"]["posts"]]
        assert open_post["id"] in ids, ids
        assert closed_id not in ids, ids
