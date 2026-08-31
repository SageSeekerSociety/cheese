"""Which connector build the server serves (pure filesystem, no device)."""

import hashlib
import os

from app.domain.agent import connector_build


def _serve(tmp_path, target: str, payload: bytes) -> str:
    """Put a connector binary where the server publishes it; return its sha256."""
    binary = tmp_path / target / "cheesehost"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_served_digest_is_the_hash_of_the_published_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    want = _serve(tmp_path, "linux-amd64", b"connector bytes")
    assert connector_build.served_digest("linux-amd64") == want


def test_served_digest_is_none_when_that_target_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _serve(tmp_path, "linux-amd64", b"connector bytes")
    assert connector_build.served_digest("darwin-arm64") is None
    # A target we never publish is not a path to go looking down.
    assert connector_build.served_digest("../../etc") is None


def test_served_digest_follows_a_rebuilt_binary(tmp_path, monkeypatch):
    """A digest cached for the life of the process would answer for a file it no
    longer describes — and that answer decides whether every connected machine is
    told to reinstall itself."""
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    _serve(tmp_path, "linux-amd64", b"old build")
    first = connector_build.served_digest("linux-amd64")
    # Same length, so only the modification time distinguishes them.
    want = _serve(tmp_path, "linux-amd64", b"new build")
    binary = tmp_path / "linux-amd64" / "cheesehost"
    stat = binary.stat()
    os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = connector_build.served_digest("linux-amd64")
    assert second == want and second != first


def test_has_any_build_reports_whether_there_is_anything_to_hand_out(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(connector_build, "dist_dir", lambda: tmp_path)
    assert connector_build.has_any_build() is False
    _serve(tmp_path, "darwin-arm64", b"connector bytes")
    assert connector_build.has_any_build() is True
