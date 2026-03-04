from __future__ import annotations

import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_upload_attachment_shape(python_client: AsyncClient) -> None:
    resp = await python_client.post(
        "/attachments",
        files={"file": ("test.txt", b"hello", "text/plain")},
        headers=USER_HEADER,
    )
    assert resp.status_code in (201, 401)
    if resp.status_code != 201:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]


@pytest.mark.anyio
async def test_python_get_attachment_shape(python_client: AsyncClient) -> None:
    resp = await python_client.get("/attachments/1", headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "attachment" in body["data"]
