import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_get_user_identity_shape(
    authed_client: AsyncClient, agent_user_id: int
) -> None:
    """GET /users/{userId}/identity 响应结构检查。"""
    resp = await authed_client.get(f"/users/{agent_user_id}/identity")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "hasIdentity" in data
    assert "identity" in data


@pytest.mark.anyio
async def test_python_put_user_identity_shape(
    authed_client: AsyncClient, agent_user_id: int
) -> None:
    """PUT /users/{userId}/identity 响应结构检查。"""
    payload = {
        "realName": "Alice",
        "studentId": "20250001",
        "grade": "2025",
        "major": "CS",
        "className": "1",
    }
    resp = await authed_client.put(f"/users/{agent_user_id}/identity", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "identity" in data


@pytest.mark.anyio
async def test_python_get_user_identity_access_logs_shape(
    authed_client: AsyncClient, agent_user_id: int
) -> None:
    """GET /users/{userId}/identity/access-logs 响应结构检查。"""
    resp = await authed_client.get(
        f"/users/{agent_user_id}/identity/access-logs", params={"pageSize": 10}
    )
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "logs" in data
    assert "page" in data

    page = data["page"]
    for key in ("pageStart", "pageSize", "hasMore", "nextStart", "total"):
        assert key in page
