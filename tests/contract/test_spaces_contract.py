import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_space_not_found(python_client: AsyncClient) -> None:
    """GET /spaces/{id} 不存在时返回 404/400/422。"""
    resp = await python_client.get("/spaces/0")
    assert resp.status_code in (404, 400, 422)


@pytest.mark.anyio
async def test_python_get_space_shape(python_client: AsyncClient) -> None:
    """GET /spaces/{id} 结构检查。"""
    resp = await python_client.get(
        "/spaces/1", params={"queryMyRank": False, "queryCategories": True}
    )
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "space" in data
    # categories may be None or list depending on data
    assert "categories" in data


@pytest.mark.anyio
async def test_python_get_spaces_shape(python_client: AsyncClient) -> None:
    """GET /spaces 列表结构检查。"""
    resp = await python_client.get("/spaces", params={"pageSize": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "spaces" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page


@pytest.mark.anyio
async def test_python_list_space_categories_shape(python_client: AsyncClient) -> None:
    """GET /spaces/{id}/categories 结构检查。"""
    resp = await python_client.get("/spaces/1/categories", params={"includeArchived": False})
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "categories" in data
