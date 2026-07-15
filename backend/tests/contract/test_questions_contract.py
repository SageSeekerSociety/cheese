import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_search_questions_shape(authed_client: AsyncClient) -> None:
    """GET /questions 列表结构检查。"""
    resp = await authed_client.get(
        "/questions", params={"page_size": 10, "page_start": 0}
    )
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "questions" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_get_question_shape(authed_client: AsyncClient) -> None:
    """GET /questions/{id} 结构检查。"""
    resp = await authed_client.get("/questions/1")
    assert resp.status_code in (200, 404)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "question" in data


@pytest.mark.anyio
async def test_python_add_question_shape(authed_client: AsyncClient) -> None:
    """POST /questions 结构检查。``type`` 是数值枚举（知是约定），非字符串。"""
    payload = {
        "title": "Test",
        "content": "Test content",
        "type": 0,
        "topics": [],
        "groupId": None,
        "bounty": 0,
    }
    resp = await authed_client.post("/questions", json=payload)
    assert resp.status_code == 201

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]
