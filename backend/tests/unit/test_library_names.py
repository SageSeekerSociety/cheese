"""资料库的名字分配：名字就是文件的身份，所以撞名不覆盖。

分配名字这一步 **就是** 写入本身（`open(..., "xb")`）。先查再写会在两次同名上传同时
在路上时丢掉一份——而那正是这个函数存在的理由。
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import NotFoundError
from app.domain.library import service as library


@pytest.fixture
def project(monkeypatch, tmp_path) -> uuid.UUID:
    monkeypatch.setattr(library.settings, "workspace_root", str(tmp_path))
    return uuid.uuid4()


def test_a_taken_name_takes_the_next_number(project):
    assert library.write_library_file(project, "预算表.xlsx", b"a") == "预算表.xlsx"
    assert library.write_library_file(project, "预算表.xlsx", b"b") == "预算表(2).xlsx"
    assert library.write_library_file(project, "预算表.xlsx", b"c") == "预算表(3).xlsx"
    assert library.read_library_file(project, "预算表.xlsx") == b"a"
    assert library.read_library_file(project, "预算表(3).xlsx") == b"c"


def test_a_name_without_a_suffix(project):
    assert library.write_library_file(project, "README", b"a") == "README"
    assert library.write_library_file(project, "README", b"b") == "README(2)"


def test_same_name_at_the_same_time_loses_neither(project):
    contents = [f"payload-{i}".encode() for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        names = list(
            pool.map(
                lambda data: library.write_library_file(project, "同名.txt", data),
                contents,
            )
        )

    assert len(set(names)) == len(contents)
    assert sorted(library.read_library_file(project, n) for n in names) == sorted(
        contents
    )
    assert len(library.list_library_files(project)) == len(contents)


def test_reading_a_file_the_library_does_not_have(project):
    with pytest.raises(NotFoundError):
        library.read_library_file(project, "没有这份.xlsx")
