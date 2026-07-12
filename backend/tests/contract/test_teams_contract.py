import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_team_not_found(authed_client: AsyncClient) -> None:
    """Python 后端：GET /teams/{id} 非法/不存在 id 时返回 400（知是约定 id>0）。"""
    resp = await authed_client.get("/teams/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_teams_shape(
    authed_client: AsyncClient, seeded_team: int
) -> None:
    """Python 后端：GET /teams 列表接口基本结构检查。

    知是的 team 列表分页是**游标式**：page 里只有 pageStart/pageSize/hasMore/
    nextStart，没有 total。"""
    resp = await authed_client.get("/teams", params={"pageSize": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "teams" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart"):
        assert key in page


@pytest.mark.anyio
async def test_python_get_my_teams_shape(
    authed_client: AsyncClient, seeded_team: int
) -> None:
    """Python 后端：GET /teams/my-teams 基本结构检查。"""
    resp = await authed_client.get("/teams/my-teams")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "teams" in data


@pytest.mark.anyio
async def test_python_get_team_members_shape(
    authed_client: AsyncClient, seeded_team: int
) -> None:
    """Python 后端：GET /teams/{teamId}/members 结构检查。"""
    resp = await authed_client.get(
        f"/teams/{seeded_team}/members", params={"queryRealNameStatus": False}
    )
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "members" in data
    assert "allMembersVerified" in data
