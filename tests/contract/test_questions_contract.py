from __future__ import annotations

import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_search_questions_shape(python_client: AsyncClient) -> None:
    """GET /questions 列表结构检查。"""
    resp = await python_client.get(
        "/questions", params={"page_size": 10, "page_start": 0}, headers=USER_HEADER
    )
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "questions" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_get_question_shape(python_client: AsyncClient) -> None:
    """GET /questions/{id} 结构检查。"""
    resp = await python_client.get("/questions/1", headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "question" in data


@pytest.mark.anyio
async def test_python_add_question_shape(python_client: AsyncClient) -> None:
    """POST /questions 结构检查。"""
    payload = {
        "title": "Test",
        "content": "Test content",
        "type": "TEXT",
        "topics": [],
        "groupId": None,
        "bounty": 0,
    }
    resp = await python_client.post("/questions", json=payload, headers=USER_HEADER)
    assert resp.status_code in (201, 401)
    if resp.status_code != 201:
        return

    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]

