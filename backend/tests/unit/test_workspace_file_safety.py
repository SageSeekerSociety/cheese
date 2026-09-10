"""文件面板 data safety: the panel must not be able to destroy a file.

Three ways it could, all reproduced before these tests existed:

* a binary file opened as text came back with every undecodable byte replaced,
  and 保存 wrote the replacements to disk (2048 bytes in, 4096 bytes out, new md5);
* a save carried no notion of which version it was based on, so a human pressing
  保存 erased whatever 芝士 had written to the same file in between, silently;
* one dangling symlink in the worktree took the whole file listing down with it.

The tests drive the workspace service directly against a plain directory —
``_tree`` is the seam that decides *which* directory an operation works in, and
these behaviours have nothing to do with git.
"""

import hashlib
import uuid

import pytest

from app.core.errors import ConflictError, ValidationError
from app.domain.workspace import service as ws
from app.domain.workspace.textfile import MAX_TEXT_BYTES, content_version

PID = uuid.uuid4()

# 2 KiB covering every byte value: NULs, and sequences utf-8 cannot decode.
BINARY = bytes(range(256)) * 8


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Point every workspace file operation at a plain directory."""
    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr(ws, "_tree", lambda project_id, topic_id=None: root)
    return root


def test_binary_file_opens_read_only_instead_of_as_text(tree):
    (tree / "app.bin").write_bytes(BINARY)

    got = ws.read_text_file(PID, "app.bin")

    assert got["binary"] is True
    # No text handed out at all — a text editor is what corrupted it.
    assert got["content"] is None
    assert got["bytes"] == len(BINARY)


def test_saving_a_binary_file_as_text_is_refused_and_the_bytes_survive(tree):
    (tree / "app.bin").write_bytes(BINARY)
    before = hashlib.md5(BINARY).hexdigest()

    # What the old panel did: decode leniently, then write the result back.
    mangled = (tree / "app.bin").read_bytes().decode("utf-8", errors="replace")
    with pytest.raises(ValidationError):
        ws.write_file(PID, "app.bin", mangled)

    after = (tree / "app.bin").read_bytes()
    assert hashlib.md5(after).hexdigest() == before
    assert len(after) == len(BINARY)


def test_text_in_a_non_utf8_encoding_is_not_offered_for_editing(tree):
    # Saving it as utf-8 would transcode the file; read-only is the honest answer.
    (tree / "gbk.txt").write_bytes("中文内容".encode("gbk"))

    assert ws.read_text_file(PID, "gbk.txt")["binary"] is True


def test_ordinary_text_still_reads_and_writes(tree):
    (tree / "a.py").write_text("print('hi')\n", encoding="utf-8")

    got = ws.read_text_file(PID, "a.py")
    assert got["content"] == "print('hi')\n"
    assert got["binary"] is False and got["too_large"] is False

    new_version = ws.write_file(
        PID, "a.py", "print('bye')\n", expected_version=got["version"]
    )
    assert (tree / "a.py").read_text(encoding="utf-8") == "print('bye')\n"
    # The returned version matches the file now on disk, so a second save in the
    # same editing session does not need a re-read.
    assert new_version == ws.read_text_file(PID, "a.py")["version"]


def test_a_save_based_on_a_stale_read_is_rejected_not_applied(tree):
    (tree / "note.txt").write_text("原始内容\n", encoding="utf-8")
    human_read = ws.read_text_file(PID, "note.txt")

    # 芝士 writes the same file while the human is typing.
    ws.write_file(PID, "note.txt", "芝士这一轮写进去的\n")

    with pytest.raises(ConflictError):
        ws.write_file(
            PID,
            "note.txt",
            human_read["content"] + "人加的一行\n",
            expected_version=human_read["version"],
        )

    # The rejected save changed nothing: 芝士's work is still there.
    assert (tree / "note.txt").read_text(encoding="utf-8") == "芝士这一轮写进去的\n"


def test_overwrite_after_seeing_the_conflict_is_allowed(tree):
    # The human chose 「仍然覆盖保存」 — the panel then sends no version.
    (tree / "note.txt").write_text("芝士写的\n", encoding="utf-8")

    ws.write_file(PID, "note.txt", "人坚持要的内容\n", expected_version=None)

    assert (tree / "note.txt").read_text(encoding="utf-8") == "人坚持要的内容\n"


def test_creating_a_new_file_is_not_a_conflict(tree):
    ws.write_file(PID, "sub/new.txt", "第一次写\n", expected_version=None)
    assert (tree / "sub" / "new.txt").read_text(encoding="utf-8") == "第一次写\n"


def test_a_write_expecting_a_file_that_is_gone_conflicts(tree):
    # The version was read from a file that no longer exists; recreating it
    # blindly would resurrect content someone deliberately deleted.
    with pytest.raises(ConflictError):
        ws.write_file(PID, "ghost.txt", "内容\n", expected_version="deadbeefdeadbeef")


def test_identical_content_is_not_reported_as_a_conflict(tree):
    (tree / "same.txt").write_text("一样的\n", encoding="utf-8")
    version = ws.read_text_file(PID, "same.txt")["version"]

    # Someone rewrote the file with byte-identical content — nothing was lost.
    ws.write_file(PID, "same.txt", "一样的\n")
    ws.write_file(PID, "same.txt", "人的新内容\n", expected_version=version)

    assert (tree / "same.txt").read_text(encoding="utf-8") == "人的新内容\n"


def test_a_dangling_symlink_does_not_break_the_file_listing(tree):
    (tree / "real.txt").write_text("ok\n", encoding="utf-8")
    (tree / "dangling").symlink_to("/nonexistent/target")

    listed = {f["path"] for f in ws.list_files(PID)}

    assert "real.txt" in listed
    assert "dangling" in listed


def test_an_oversized_file_is_never_read_into_the_response(tree):
    big = tree / "big.bin"
    big.write_bytes(b"a" * (MAX_TEXT_BYTES + 1))

    got = ws.read_text_file(PID, "big.bin")

    assert got["too_large"] is True
    assert got["content"] is None
    assert got["bytes"] == MAX_TEXT_BYTES + 1


def test_version_identifies_content_not_the_moment_it_was_written():
    assert content_version(b"abc") == content_version(b"abc")
    assert content_version(b"abc") != content_version(b"abd")


def test_linked_worktree_git_pointer_is_not_a_user_file(tree):
    (tree / ".git").write_text("gitdir: ../../repo/.git/worktrees/task_123\n")
    (tree / "note.txt").write_text("visible\n")
    assert [file["path"] for file in ws.list_files(PID)] == ["note.txt"]
