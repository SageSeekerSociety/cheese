import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_team_not_found(python_client: AsyncClient) -> None:
    """Python 后端：GET /teams/{id} 不存在时返回 404。"""
    resp = await python_client.get("/teams/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_teams_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /teams 列表接口基本结构检查。"""
    resp = await python_client.get("/teams", params={"pageSize": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "teams" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page


@pytest.mark.anyio
async def test_python_get_my_teams_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /teams/my-teams 基本结构检查。"""
    # X-User-Id 用 1 作为占位，实际需由调用方设置为合法用户 ID
    resp = await python_client.get("/teams/my-teams", headers={"X-User-Id": "1"})
    # 未实现 membership 查询时，可能返回空列表，但结构应正确
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "teams" in data


@pytest.mark.anyio
async def test_python_get_team_members_shape(python_client: AsyncClient) -> None:
    """Python 后端：GET /teams/{teamId}/members 结构检查（不依赖具体数据）。"""
    # teamId 使用 1 作为占位，实际是否存在由调用环境决定
    resp = await python_client.get("/teams/1/members", params={"queryRealNameStatus": False})
    # 允许 200 或 404，只有 200 时检查结构
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "members" in data
    assert "allMembersVerified" in data
