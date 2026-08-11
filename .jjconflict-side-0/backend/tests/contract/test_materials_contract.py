import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_upload_material_shape(authed_client: AsyncClient) -> None:
    # ``type`` (Form) is required and must match the file's MIME family;
    # application/octet-stream matches the "file" type.
    resp = await authed_client.post(
        "/materials",
        files={"file": ("file.bin", b"data", "application/octet-stream")},
        data={"type": "file"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]


@pytest.mark.anyio
async def test_python_get_material_shape(authed_client: AsyncClient) -> None:
    # Upload one first so the lookup has a concrete target (DB is truncated
    # per-test).
    upload = await authed_client.post(
        "/materials",
        files={"file": ("file.bin", b"data", "application/octet-stream")},
        data={"type": "file"},
    )
    assert upload.status_code == 201
    material_id = upload.json()["data"]["id"]

    resp = await authed_client.get(f"/materials/{material_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "material" in body["data"]
