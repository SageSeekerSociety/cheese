"""A starter-code archive becomes the commit its publisher meant (#4767 §二)."""

import io
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app.domain.project import starter

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def _tree(tmp_path: Path, data: bytes) -> dict[str, bytes]:
    """Every file of the starter commit built from ``data``, and its bytes."""
    archive = tmp_path / "starter.zip"
    archive.write_bytes(data)
    repository = tmp_path / "starter.git"
    starter.build(archive, "Starter code", WHEN, repository)

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True, check=True
        ).stdout

    names = git("ls-tree", "-r", "-z", "--name-only", "main").split(b"\0")
    return {
        name.decode(): git("cat-file", "blob", f"main:{name.decode()}")
        for name in names
        if name
    }


def test_a_name_saved_in_gbk_unpacks_as_the_chinese_it_was(tmp_path: Path):
    """Zips made on a Chinese Windows store names in GBK without the UTF-8 flag."""
    gbk = "实验/说明.txt".encode("gbk")
    placeholder = b"x" * len(gbk)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(placeholder.decode(), b"data")
    # The same entry under a GBK name, in both headers, with no UTF-8 flag.
    patched = buffer.getvalue().replace(placeholder, gbk)

    # 实验/ is the archive's one top folder, so it goes.
    assert _tree(tmp_path, patched) == {"说明.txt": b"data"}


def test_files_beside_the_top_folder_keep_it(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lab/main.py", b"code")
        archive.writestr("README.md", b"readme")

    assert _tree(tmp_path, buffer.getvalue()) == {
        "README.md": b"readme",
        "lab/main.py": b"code",
    }


def test_a_path_as_long_as_upload_allows_still_becomes_a_commit(tmp_path: Path):
    """What the upload check accepts commits wherever the job runs: a path of
    4095 bytes (16 names of 255) is no longer than the check allows, and no
    directory the job works in counts against it."""
    long = "/".join(["n" * 254 + str(i % 10) for i in range(16)])
    assert len(long.encode()) == 4095
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(long, b"deep")
        archive.writestr("README.md", b"readme")

    assert starter.check(io.BytesIO(buffer.getvalue())) == 2
    assert _tree(tmp_path, buffer.getvalue()) == {long: b"deep", "README.md": b"readme"}
