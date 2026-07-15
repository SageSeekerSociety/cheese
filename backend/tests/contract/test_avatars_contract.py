import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_create_avatar_shape(authed_client: AsyncClient) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    resp = await authed_client.post(
        "/avatars",
        files={"avatar": ("avatar.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "avatarId" in body["data"]


@pytest.mark.anyio
async def test_python_get_avatars_shape(authed_client: AsyncClient) -> None:
    # The list route is registered at "/" under the /avatars prefix, so the
    # canonical path carries a trailing slash; a bare /avatars is a 405.
    resp = await authed_client.get("/avatars/", params={"type": "predefined"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "avatarIds" in body["data"]
