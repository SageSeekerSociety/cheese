import pytest
from httpx import AsyncClient

USER_HEADER = {"X-User-Id": "1"}


@pytest.mark.anyio
async def test_python_upload_material_shape(python_client: AsyncClient) -> None:
    resp = await python_client.post(
        "/materials",
        files={"file": ("file.bin", b"data", "application/octet-stream")},
        headers=USER_HEADER,
    )
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]


@pytest.mark.anyio
async def test_python_get_material_shape(python_client: AsyncClient) -> None:
    resp = await python_client.get("/materials/1", headers=USER_HEADER)
    assert resp.status_code in (200, 401)
    if resp.status_code != 200:
        return
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "material" in body["data"]
