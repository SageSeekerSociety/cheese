import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_python_upload_attachment_shape(authed_client: AsyncClient) -> None:
    # ``type`` (Form) is required and must match the file's MIME family;
    # text/plain matches the "file" type.
    resp = await authed_client.post(
        "/attachments",
        files={"file": ("test.txt", b"hello", "text/plain")},
        data={"type": "file"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "id" in body["data"]


@pytest.mark.anyio
async def test_python_get_attachment_shape(authed_client: AsyncClient) -> None:
    # Upload one first so the lookup targets a concrete row (DB truncated
    # per-test).
    upload = await authed_client.post(
        "/attachments",
        files={"file": ("test.txt", b"hello", "text/plain")},
        data={"type": "file"},
    )
    assert upload.status_code == 201
    attachment_id = upload.json()["data"]["id"]

    resp = await authed_client.get(f"/attachments/{attachment_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "data"}
    assert "attachment" in body["data"]
