import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_list_knowledge_shape(
    authed_client: AsyncClient, seeded_team: int
) -> None:
    """GET /knowledge 列表结构检查。列表按 team 过滤，且要求调用者是该 team 成员。"""
    resp = await authed_client.get(
        "/knowledge",
        params={"teamId": seeded_team, "pageSize": 10},
    )
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "knowledges" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_create_knowledge_shape(
    authed_client: AsyncClient, seeded_team: int
) -> None:
    """POST /knowledge 结构检查。``teamId`` 必填，且要求调用者是该 team 成员。"""
    payload = {
        "name": "Test",
        "type": "TEXT",
        "content": "content",
        "description": "desc",
        "teamId": seeded_team,
    }
    resp = await authed_client.post("/knowledge", json=payload)
    assert resp.status_code == 201

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "knowledge" in body["data"]
