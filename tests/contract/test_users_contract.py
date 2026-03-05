import pytest
from httpx import AsyncClient


USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_get_user_identity_shape(python_client: AsyncClient) -> None:
    """GET /users/{userId}/identity 响应结构检查。"""
    resp = await python_client.get("/users/1/identity", headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "hasIdentity" in data
    assert "identity" in data


@pytest.mark.anyio
async def test_python_put_user_identity_shape(python_client: AsyncClient) -> None:
    """PUT /users/{userId}/identity 响应结构检查。"""
    payload = {
        "realName": "Alice",
        "studentId": "20250001",
        "grade": "2025",
        "major": "CS",
        "className": "1",
    }
    resp = await python_client.put("/users/1/identity", json=payload, headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "identity" in data


@pytest.mark.anyio
async def test_python_get_user_identity_access_logs_shape(python_client: AsyncClient) -> None:
    """GET /users/{userId}/identity/access-logs 响应结构检查。"""
    resp = await python_client.get(
        "/users/1/identity/access-logs", params={"pageSize": 10}, headers=USER_HEADER
    )
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "logs" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page
