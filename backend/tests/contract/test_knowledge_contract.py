import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1", "Authorization": "Bearer token"}


@pytest.mark.anyio
async def test_python_list_knowledge_shape(python_client: AsyncClient) -> None:
    """GET /knowledge 列表结构检查。"""
    resp = await python_client.get(
        "/knowledge",
        params={"teamId": 1, "pageSize": 10},
        headers=USER_HEADER,
    )
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "knowledges" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_create_knowledge_shape(python_client: AsyncClient) -> None:
    """POST /knowledge 结构检查。"""
    payload = {
        "name": "Test",
        "type": "TEXT",
        "content": "content",
        "description": "desc",
    }
    resp = await python_client.post("/knowledge", json=payload, headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "knowledge" in body["data"]
