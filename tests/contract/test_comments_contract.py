from __future__ import annotations

import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_get_comments_shape(python_client: AsyncClient) -> None:
    resp = await python_client.get(
        "/comments/question/1", params={"page_size": 10}, headers=USER_HEADER
    )
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    data = body["data"]
    assert "comments" in data
    assert "page" in data


@pytest.mark.anyio
async def test_python_create_comment_shape(python_client: AsyncClient) -> None:
    resp = await python_client.post(
        "/comments/question/1",
        json={"content": "hi"},
        headers=USER_HEADER,
    )
    assert resp.status_code in (201, 401)
    if resp.status_code != 201:
        return
    body = resp.json()
    assert "id" in body["data"]
