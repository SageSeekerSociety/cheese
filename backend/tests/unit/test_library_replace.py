"""替换一份资料的字节：写坏的那一半不出现在资料库里，一名字上的替换排成队。

替换是「写到旁边再换过去」：读的人不会读到半份，写了一半就崩的那一份也不该留在
资料库里——早先它写的就是资料库根目录里的点文件，而从 Python 3.13 起
`Path.rglob("*")` 连点文件一起列，于是那一份既上资料库页、又要谁去删它才会没。

换过去的这一对动作是**读出来再写回去**：`superseded_at` 为空的那一行只有一行
（`uq_library_files_current`），两个人同时替换同一份时两边都会以为自己读到的就是
现在这一份。按 project:name 取的那把锁把同一个名字上的替换排成队，这就是它在前
的原因。真的撞上了（锁管不到的那些写入），答案是 409，不是 500。

数据库那一半为什么是单元测试：要钉住的是**一次请求里的次序**，而集成测试的每一
个用例都跑在一条共享连接的一个事务里（`tests/integration/conftest.py`），两个真正
重叠的请求在那里写不出来；写得出来的是语句的次序，也就是这里读的。
`tests/unit/test_feedback_proposal_lock.py` 是同一个理由的同一件事。
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.domain.library import records
from app.domain.library import service as library


@pytest.fixture
def project(monkeypatch, tmp_path) -> uuid.UUID:
    monkeypatch.setattr(library.settings, "workspace_root", str(tmp_path))
    return uuid.uuid4()


def _root(project: uuid.UUID) -> Path:
    return library.library_root(project)


def _staging(project: uuid.UUID) -> Path:
    return Path(library.settings.workspace_root) / ".library-staging" / str(project)


def _names_in(project: uuid.UUID) -> list[str]:
    return sorted(entry.name for entry in _root(project).iterdir())


def test_a_replace_leaves_only_the_file_it_replaced(project):
    library.write_library_file(project, "预算.xlsx", b"v1")
    library.overwrite_library_file(project, "预算.xlsx", b"v2")

    assert library.read_library_file(project, "预算.xlsx") == b"v2"
    assert _names_in(project) == ["预算.xlsx"]
    assert [f["path"] for f in library.list_library_files(project)] == ["预算.xlsx"]


def test_a_replace_that_dies_halfway_leaves_nothing_behind(project, monkeypatch):
    library.write_library_file(project, "预算.xlsx", b"v1")

    def die(self, data):
        raise OSError("disk full")

    writes = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", die)
    with pytest.raises(OSError):
        library.overwrite_library_file(project, "预算.xlsx", b"v2")
    # 只把这一处改回来：`monkeypatch.undo()` 会连 `project` 那个 fixture 挂上的
    # workspace_root 一起还原，那之后读的就是真的工作区了。
    monkeypatch.setattr(Path, "write_bytes", writes)

    # 还是原来那一份，而写坏的那一半哪儿都没有：资料库里没有它，暂存目录里也没有。
    assert library.read_library_file(project, "预算.xlsx") == b"v1"
    assert _names_in(project) == ["预算.xlsx"]
    assert list(_staging(project).iterdir()) == []


def test_the_leftovers_of_an_older_replace_are_not_library_files(project):
    library.write_library_file(project, "预算.xlsx", b"v1")
    # 早先的写法留在根目录里的那一份：`.<名字>.<uuid hex>`。
    (_root(project) / f".预算.xlsx.{uuid.uuid4().hex}").write_bytes(b"half")

    assert [f["path"] for f in library.list_library_files(project)] == ["预算.xlsx"]


def test_a_name_that_starts_with_a_dot_is_still_a_library_file(project):
    # 从工作区存进资料库的文件用原名（`room_files.copy_into_room`），`.gitignore`
    # 是正经的一条——不列它才是丢了东西。
    library.write_library_file(project, ".gitignore", b"node_modules")

    assert [f["path"] for f in library.list_library_files(project)] == [".gitignore"]


class _Recorder:
    """A session that records every statement instead of running it."""

    def __init__(self, *, flush_fails: bool = False) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.flush_fails = flush_fails

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None

    async def scalar(self, statement):
        self.calls.append((str(statement), None))
        return None

    def add(self, row) -> None:
        pass

    async def flush(self) -> None:
        if self.flush_fails:
            raise IntegrityError(
                "duplicate key value violates unique constraint",
                None,
                Exception("uq_library_files_current"),
            )


async def test_a_replace_takes_the_name_lock_before_it_reads(project):
    library.write_library_file(project, "预算.xlsx", b"v1")
    session = _Recorder()

    await records.replace(
        session, project_id=project, name="预算.xlsx", data=b"v2", by="user-1"
    )

    first, params = session.calls[0]
    assert "pg_advisory_xact_lock" in first
    assert params == {"key": f"library-file:{project}:预算.xlsx"}
    # `keep_replaced` 读「现在这一份」在那之后：锁在路上就没用了。
    assert len(session.calls) > 1


async def test_a_lost_race_comes_back_as_a_conflict_not_a_crash(project):
    library.write_library_file(project, "预算.xlsx", b"v1")
    session = _Recorder(flush_fails=True)

    with pytest.raises(ConflictError) as refused:
        await records.replace(
            session, project_id=project, name="预算.xlsx", data=b"v2", by="user-1"
        )

    assert refused.value.status_code == 409
