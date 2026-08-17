"""Locally-stored uploads are reachable, and cannot become a script host.

Two things are being pinned. The first is that they are served at all: the URL
`LocalStorageBackend` writes into `material.url` had nothing behind it, so every
素材 and every inline 赛题 image 404'd on a container deployment.

The second is the reason this is a route and not `StaticFiles`. `type=file`
uploads accept any `text/*` mime, so a user can store HTML; served inline from
the app's origin that is stored XSS against a frontend holding its JWT in
localStorage. Serving must therefore be inert by construction, and "inert" is a
property no type checker can see — hence these.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


@pytest.fixture
def upload_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    return tmp_path


def _write(root: Path, rel: str, content: bytes) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_an_uploaded_image_is_served(api_client: TestClient, upload_root: Path) -> None:
    _write(upload_root, "materials/image/2026/08/13/abc.png", b"\x89PNG\r\n\x1a\n")

    resp = api_client.get("/uploads/materials/image/2026/08/13/abc.png")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content.startswith(b"\x89PNG")


def test_the_response_is_inert(api_client: TestClient, upload_root: Path) -> None:
    _write(upload_root, "materials/image/x.png", b"\x89PNG\r\n\x1a\n")

    resp = api_client.get("/uploads/materials/image/x.png")

    # nosniff: the browser must not go looking for a better type than we sent.
    assert resp.headers["x-content-type-options"] == "nosniff"
    # …and even a document that slipped through gets no capabilities at all.
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp


@pytest.mark.parametrize("name", ["evil.html", "evil.svg", "evil.xml", "evil.js"])
def test_executable_types_are_never_served_as_themselves(
    api_client: TestClient, upload_root: Path, name: str
) -> None:
    """The XSS case, stated as a test.

    An uploaded `.html` or `.svg` is still stored and still downloadable — it
    just stops being served with a content type a browser will execute in this
    origin. Without this the file is script running next to the user's token.
    """
    _write(upload_root, f"materials/file/{name}", b"<script>alert(1)</script>")

    resp = api_client.get(f"/uploads/materials/file/{name}")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")


@pytest.mark.parametrize(
    "attack",
    [
        "../../../etc/passwd",
        "materials/../../../etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ],
)
def test_traversal_cannot_leave_the_storage_root(
    api_client: TestClient, upload_root: Path, attack: str
) -> None:
    outside = upload_root.parent / "secret.txt"
    outside.write_text("do not serve me")

    resp = api_client.get(f"/uploads/{attack}")

    assert resp.status_code in (404, 400)
    assert b"do not serve me" not in resp.content


def test_a_symlink_out_of_the_tree_is_refused(
    api_client: TestClient, upload_root: Path
) -> None:
    """Containment is checked AFTER resolve(), so a planted symlink loses too.

    Traversal in the URL is the obvious attack; a symlink inside the storage
    directory is the one that survives a naive `startswith` check.
    """
    secret = upload_root.parent / "secret.txt"
    secret.write_text("do not serve me")
    (upload_root / "materials").mkdir(parents=True, exist_ok=True)
    (upload_root / "materials" / "link.txt").symlink_to(secret)

    resp = api_client.get("/uploads/materials/link.txt")

    assert resp.status_code == 404


def test_a_missing_file_is_a_404(api_client: TestClient, upload_root: Path) -> None:
    assert api_client.get("/uploads/materials/nope.png").status_code == 404
