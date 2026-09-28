"""学习看板的筛选栏：非成员读不到，成员读得到。

`GET /spaces/{spaceId}/analytics/learning/filters` 报两样东西：行数据（成员、
计数）和课程级的分类名（`knowledgePoints`，即 ``space_categories``）。行数据由
`SpaceLearningService` 逐项目过 `may_read_project`，一个都不许读就是空的；分类名
不是某个项目里的行，没有项目可逐条过 —— 挡它的只有课程级的可见性门。

这里盯的就是那道门：
- 一个不在这个板里的人，`GET /spaces/{id}` 与 `GET /spaces/{id}/topics` 都答
  404，那么 `/analytics/learning/filters` 也必须答 404，而不是把别人课程自己划的
  分类名（含 id）念出来；
- 在板里的人（创建者，或拿邀请码进来的人）照常 200，分类名还在。

红绿是这么验的：把路由上那一行 `_ensure_space_visible` 去掉，第一个用例就红。
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_name(resp) -> str | None:
    return (resp.json().get("error") or {}).get("name")


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块过了审的板子，外加一个专门起的分类名 —— 那就是被读出来的那个东西。"""
    owner = user_client.create_user()
    owner.token = user_client.login(api_client, owner.username, owner.password)

    suffix = unique_int(10000000, 99999999)
    created = create_approved_space(
        api_client,
        json={
            "name": f"Learning Filters Space ({suffix})",
            "intro": "Learning filters visibility.",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(owner.token),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    space_id = data["space"]["id"]

    marker = f"Marker-Category-{unique_int(10000000, 99999999)}"
    category = api_client.post(
        f"/spaces/{space_id}/categories",
        json={"name": marker, "description": "not for outsiders", "displayOrder": 1},
        headers=_auth(owner.token),
    )
    assert category.status_code == 201, category.text
    category_id = category.json()["data"]["category"]["id"]

    return {
        "owner": owner,
        "space_id": space_id,
        "invite_code": data["inviteCode"]["code"],
        "marker": marker,
        "category_id": category_id,
    }


class TestALearningFilterBarIsInvisibleUntilYouAreIn:
    def test_a_stranger_gets_404_and_no_category_names(
        self, board: dict, user_client: UserCreator, api_client: TestClient
    ):
        space_id = board["space_id"]
        stranger = user_client.create_user()
        stranger.token = user_client.login(
            api_client, stranger.username, stranger.password
        )

        # 同一条规矩的另外两处，先把「这个板对你不存在」立住。
        for path in (f"/spaces/{space_id}", f"/spaces/{space_id}/topics"):
            resp = api_client.get(path, headers=_auth(stranger.token))
            assert resp.status_code == 404, f"{path} -> {resp.status_code}"
            assert _error_name(resp) == "NotFoundError", resp.text

        resp = api_client.get(
            f"/spaces/{space_id}/analytics/learning/filters",
            headers=_auth(stranger.token),
        )
        assert resp.status_code == 404, resp.text
        assert _error_name(resp) == "NotFoundError", resp.text
        # 分类名（和它的 id）一个字都不许出现在这一格里。
        assert board["marker"] not in resp.text
        assert str(board["category_id"]) not in resp.text

    def test_the_owner_still_reads_the_category_names(
        self, board: dict, api_client: TestClient
    ):
        owner = board["owner"]
        resp = api_client.get(
            f"/spaces/{board['space_id']}/analytics/learning/filters",
            headers=_auth(owner.token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        options = {p["name"]: p["categoryId"] for p in data["knowledgePoints"]}
        assert options.get(board["marker"]) == board["category_id"], data

    def test_someone_who_redeemed_the_code_reads_them_too(
        self, board: dict, user_client: UserCreator, api_client: TestClient
    ):
        """门是「是不是成员」，不是「是不是创建者」—— 和本文件其它空间路由一致。"""
        joiner = user_client.create_user()
        joiner.token = user_client.login(api_client, joiner.username, joiner.password)
        joined = api_client.post(
            "/spaces/join",
            json={"code": board["invite_code"]},
            headers=_auth(joiner.token),
        )
        assert joined.status_code == 200, joined.text

        resp = api_client.get(
            f"/spaces/{board['space_id']}/analytics/learning/filters",
            headers=_auth(joiner.token),
        )
        assert resp.status_code == 200, resp.text
        names = {p["name"] for p in resp.json()["data"]["knowledgePoints"]}
        assert board["marker"] in names, resp.text
