from urllib.parse import quote

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


@pytest.mark.anyio
async def test_python_download_attachment_serves_a_non_ascii_name(
    authed_client: AsyncClient,
) -> None:
    # The name a person typed is what has to come back. An HTTP header is
    # latin-1, so a name outside that repertoire cannot be written into one
    # as-is — the download has to carry it percent-encoded instead of raising.
    name = "需求 文档.txt"
    upload = await authed_client.post(
        "/attachments",
        files={"file": (name, b"hello", "text/plain")},
        data={"type": "file"},
    )
    assert upload.status_code == 201
    attachment_id = upload.json()["data"]["id"]

    resp = await authed_client.get(f"/attachments/{attachment_id}/download")

    assert resp.status_code == 200
    assert resp.content == b"hello"
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''" + quote(name, safe="")
    )
    # The same escape also covers a name carrying a quote or a newline — the
    # reason the old form was header syntax rather than a filename. Not a
    # separate test: httpx's multipart encoder percent-encodes those itself
    # before they reach the route, so no request through this client can hand
    # the route a literal one.
