"""A starter-code archive becomes the commit its publisher meant (#4767 §二)."""

import io
import os
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.errors import UnprocessableEntityError
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


#: Unpacked trees and what the starter commit must hold of each: exactly what
#: `git add -A` commits from that tree, asked of git itself below.
IGNORE_CASES = {
    "plain": {".gitignore": b".env\n", ".env": b"S=1", "a.txt": b"a"},
    "folder": {
        ".gitignore": b"node_modules/\n",
        "node_modules/x/i.js": b"1",
        "a.txt": b"a",
    },
    "nested-negation": {
        "src/.gitignore": b"*.tmp\n!keep.tmp\n",
        "src/x.tmp": b"x",
        "src/keep.tmp": b"k",
        "src/a.py": b"p",
    },
    "root-negation": {
        ".gitignore": b"*.log\n!keep.log\n",
        "x.log": b"x",
        "keep.log": b"k",
    },
    "anchored": {
        ".gitignore": b"/build\n",
        "build/out": b"o",
        "src/build/kept": b"k",
    },
    "double-star": {".gitignore": b"**/cache/\n", "a/b/cache/c": b"c", "a/b/d": b"d"},
    "comments": {".gitignore": b"# notes\n\n*.o\n", "x.o": b"o", "x.c": b"c"},
    "escaped-hash": {".gitignore": b"\\#secret\n", "#secret": b"s", "public": b"p"},
    "ignores-itself": {
        ".gitignore": b".gitignore\nlocal/\n",
        "local/x": b"x",
        "a": b"a",
    },
    "excluded-folder-negated-inside": {
        ".gitignore": b"build/\n",
        "build/.gitignore": b"!keep.txt\n",
        "build/keep.txt": b"k",
        "a.txt": b"a",
    },
    "excluded-folder-negated-at-root": {
        ".gitignore": b"build/\n!build/keep.txt\n",
        "build/keep.txt": b"k",
        "a.txt": b"a",
    },
    "byte-order-mark": {
        ".gitignore": b"\xef\xbb\xbf.env\n",
        ".env": b"S",
        "a.txt": b"a",
    },
    "over-a-megabyte": {
        ".gitignore": b"#" * (1024 * 1024 + 1) + b"\n.env\n",
        ".env": b"S",
        "a.txt": b"a",
    },
}


def _git_add_all(tmp_path: Path, files: dict[str, bytes]) -> set[str]:
    tree = tmp_path / "tree"
    for name, data in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(data)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(["git", "-C", str(tree), *args], check=True, env=env)
    listed = subprocess.run(
        ["git", "-C", str(tree), "ls-files", "-z"],
        check=True,
        capture_output=True,
        env=env,
    ).stdout
    return {name.decode() for name in listed.split(b"\0") if name}


@pytest.mark.parametrize("case", sorted(IGNORE_CASES))
def test_the_starter_holds_exactly_what_git_add_would_commit(tmp_path: Path, case):
    files = IGNORE_CASES[case]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        # A second top entry, so the archive's own top folder is never stripped.
        archive.writestr("zz-top.txt", b"z")

    (tmp_path / "built").mkdir()
    built = set(_tree(tmp_path / "built", buffer.getvalue()))

    assert built == _git_add_all(tmp_path, {**files, "zz-top.txt": b"z"})


def test_an_archive_whose_gitignore_excludes_every_file_says_so():
    """A zip that does hold files, all of them excluded by its own .gitignore,
    is refused for that reason, not as an archive with no files in it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(".gitignore", b"*\n")
        archive.writestr("main.py", b"print(1)")

    with pytest.raises(UnprocessableEntityError) as refused:
        starter.check(io.BytesIO(buffer.getvalue()))

    assert refused.value.message.key == "starterAllIgnored"
