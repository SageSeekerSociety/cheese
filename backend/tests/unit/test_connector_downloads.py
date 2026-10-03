"""What a machine downloads to become a device: the connector for its platform."""

from fastapi.testclient import TestClient

from app.api.routes import installer
from app.domain.agent import connector_build
from app.main import app


def _publish(tmp_path, target: str, name: str, payload: bytes) -> None:
    binary = tmp_path / target / name
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(payload)


def test_windows_gets_cheesehost_exe_and_the_others_keep_cheesehost(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    monkeypatch.setattr(installer, "_dist_dir", lambda: tmp_path)
    _publish(tmp_path, "windows-amd64", "cheesehost.exe", b"windows connector")
    _publish(tmp_path, "linux-amd64", "cheesehost", b"linux connector")
    client = TestClient(app)

    windows = client.get("/connector/latest/windows-amd64/cheesehost.exe")
    assert windows.status_code == 200 and windows.content == b"windows connector"
    linux = client.get("/connector/latest/linux-amd64/cheesehost")
    assert linux.status_code == 200 and linux.content == b"linux connector"

    # Each platform has exactly one name; the other is not a second way in.
    assert client.get("/connector/latest/windows-amd64/cheesehost").status_code == 404
    assert client.get("/connector/latest/linux-amd64/cheesehost.exe").status_code == 404


async def test_the_update_check_knows_the_windows_build(tmp_path, monkeypatch):
    """A Windows connector reports target windows-amd64; the server has to find
    the build it serves there, or it can never tell that machine to update."""
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "windows-amd64", "cheesehost.exe", b"windows connector")
    assert await connector_build.published_digest("windows-amd64") is not None


def test_the_published_digest_names_the_bytes_a_machine_downloads(
    tmp_path, monkeypatch
):
    """Asking which build is published must not cost the download, and its
    answer is what a machine updating itself will end up running."""
    import hashlib

    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _publish(tmp_path, "linux-amd64", "cheesehost", b"linux connector")
    client = TestClient(app)

    asked = client.head("/connector/latest/linux-amd64/cheesehost")
    downloaded = client.get("/connector/latest/linux-amd64/cheesehost")

    assert asked.status_code == 200 and asked.content == b""
    assert asked.headers["X-Checksum-SHA256"] == (
        hashlib.sha256(downloaded.content).hexdigest()
    )
