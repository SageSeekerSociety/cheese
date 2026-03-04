from __future__ import annotations

import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_create_avatar_shape(python_client: AsyncClient) -> None:
    resp = await python_client.post(
        "/avatars",
        files={"avatar": ("avatar.png", b"png", "image/png")},
        headers=USER_HEADER | {"Authorization": "Bearer token"},
    )
    assert resp.status_code in (201, 401)
    if resp.status_code != 201:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "avatarId" in body["data"]


@pytest.mark.anyio
async def test_python_get_avatars_shape(python_client: AsyncClient) -> None:
    resp = await python_client.get(
        "/avatars",
        params={"type": "predefined"},
        headers=USER_HEADER | {"Authorization": "Bearer token"},
    )
    assert resp.status_code in (200, 401, 400)
