"""A starter-code archive unpacks to the tree its publisher meant (#4767 §二)."""

import io
import zipfile
from pathlib import Path

from app.domain.project import starter


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
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

    starter.unpack(io.BytesIO(patched), tmp_path)

    # 实验/ is the archive's one top folder, so it goes.
    assert _tree(tmp_path) == {"说明.txt": b"data"}


def test_files_beside_the_top_folder_keep_it(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lab/main.py", b"code")
        archive.writestr("README.md", b"readme")

    starter.unpack(io.BytesIO(buffer.getvalue()), tmp_path)

    assert _tree(tmp_path) == {"README.md": b"readme", "lab/main.py": b"code"}
