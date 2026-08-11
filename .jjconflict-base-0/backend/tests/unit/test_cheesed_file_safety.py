"""The compute node's file endpoints must protect data exactly like the backend's.

When ``compute_provider == "remote"`` the 文件 panel's reads and writes are
proxied to cheesed on the node, so a guard that exists only in the backend is a
guard the panel loses the moment compute moves off-box. These pin the parity.
"""

import hashlib

import pytest

from app.domain.agent import cheesed
from app.domain.workspace.textfile import MAX_TEXT_BYTES

PID = "11111111-1111-1111-1111-111111111111"
TID = "22222222-2222-2222-2222-222222222222"
BINARY = bytes(range(256)) * 8


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "ws" / PID / TID
    root.mkdir(parents=True)
    monkeypatch.setattr(cheesed, "_WORKSPACE", str(tmp_path / "ws"))
    return root


@pytest.mark.anyio
async def test_node_reports_binary_instead_of_mangled_text(tree):
    (tree / "app.bin").write_bytes(BINARY)

    data = (await cheesed.read_file(PID, TID, "app.bin"))["data"]

    assert data["binary"] is True
    assert data["content"] is None


@pytest.mark.anyio
async def test_node_refuses_a_text_save_over_a_binary_file(tree):
    (tree / "app.bin").write_bytes(BINARY)
    before = hashlib.md5(BINARY).hexdigest()

    res = await cheesed.write_file(PID, TID, {"path": "app.bin", "content": "whatever"})

    assert res == {"ok": False, "reason": "binary"}
    assert hashlib.md5((tree / "app.bin").read_bytes()).hexdigest() == before


@pytest.mark.anyio
async def test_node_rejects_a_save_based_on_a_stale_read(tree):
    (tree / "note.txt").write_text("原始内容\n", encoding="utf-8")
    version = (await cheesed.read_file(PID, TID, "note.txt"))["data"]["version"]

    await cheesed.write_file(PID, TID, {"path": "note.txt", "content": "芝士写的\n"})
    res = await cheesed.write_file(
        PID, TID, {"path": "note.txt", "content": "人写的\n", "version": version}
    )

    assert res["ok"] is False and res["reason"] == "conflict"
    assert (tree / "note.txt").read_text(encoding="utf-8") == "芝士写的\n"


@pytest.mark.anyio
async def test_node_accepts_a_save_carrying_the_current_version(tree):
    (tree / "note.txt").write_text("原始内容\n", encoding="utf-8")
    version = (await cheesed.read_file(PID, TID, "note.txt"))["data"]["version"]

    res = await cheesed.write_file(
        PID, TID, {"path": "note.txt", "content": "人写的\n", "version": version}
    )

    assert res["ok"] is True
    assert (tree / "note.txt").read_text(encoding="utf-8") == "人写的\n"
    assert (
        res["version"]
        == (await cheesed.read_file(PID, TID, "note.txt"))["data"]["version"]
    )


@pytest.mark.anyio
async def test_node_does_not_send_an_oversized_file(tree):
    (tree / "big.bin").write_bytes(b"a" * (MAX_TEXT_BYTES + 1))

    data = (await cheesed.read_file(PID, TID, "big.bin"))["data"]

    assert data["too_large"] is True
    assert data["content"] is None


@pytest.mark.anyio
async def test_node_listing_survives_a_dangling_symlink(tree):
    (tree / "real.txt").write_text("ok\n", encoding="utf-8")
    (tree / "dangling").symlink_to("/nonexistent/target")

    listed = {f["path"] for f in (await cheesed.list_files(PID, TID))["data"]}

    assert listed == {"real.txt", "dangling"}
