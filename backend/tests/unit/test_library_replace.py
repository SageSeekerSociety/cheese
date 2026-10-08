"""替换一份资料：同一个名字上的替换排成队，写坏的那一半不留下来。

替换是「写到旁边再换过去」（`blobs.LocalBlobs.put`）：读的人不会读到半份。写了一半
的那一份也不能留下——它的键是记录那一行的 id，留下之后谁也认不出它、谁也不去删它，
只会一直占着地方。

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
from app.domain.library import blobs, records
from app.domain.library.models import LibraryFileRecord


@pytest.fixture
def project(monkeypatch, tmp_path) -> uuid.UUID:
    monkeypatch.setattr(blobs.settings, "workspace_root", str(tmp_path))
    return uuid.uuid4()


def _stored(project: uuid.UUID) -> list[str]:
    """存储里现在有哪些键（叶子名），按名字排。"""
    root = Path(blobs.settings.workspace_root) / ".library-blobs" / str(project)
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.rglob("*") if entry.is_file())


def test_a_put_leaves_only_the_bytes_it_put(project):
    blobs.store(blobs.LOCAL).put(blobs.new_key(project, uuid.uuid4()), b"v1")

    assert len(_stored(project)) == 1


def test_a_put_that_dies_halfway_leaves_nothing_behind(project, monkeypatch):
    def die(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", die)

    with pytest.raises(OSError):
        blobs.store(blobs.LOCAL).put(blobs.new_key(project, uuid.uuid4()), b"v1")

    assert _stored(project) == []


def _current_row(project: uuid.UUID, name: str) -> LibraryFileRecord:
    return LibraryFileRecord(
        id=uuid.uuid4(),
        project_id=project,
        name=name,
        bytes=2,
        sha256="0" * 64,
        location=blobs.LOCAL,
        blob_key=None,
    )


class _Recorder:
    """A session that records every statement instead of running it."""

    def __init__(
        self, *, current: LibraryFileRecord, flush_fails: bool = False
    ) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.current = current
        self.flush_fails = flush_fails

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None

    async def scalar(self, statement):
        self.calls.append((str(statement), None))
        return self.current

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
    session = _Recorder(current=_current_row(project, "预算.xlsx"))

    await records.replace(
        session, project_id=project, name="预算.xlsx", data=b"v2", by="user-1"
    )

    first, params = session.calls[0]
    assert "pg_advisory_xact_lock" in first
    assert params == {"key": f"library-file:{project}:预算.xlsx"}
    # 读「现在这一份」在那之后：锁在路上就没用了。
    assert len(session.calls) > 1
    assert session.calls[1][0] != first


async def test_a_lost_race_comes_back_as_a_conflict_not_a_crash(project):
    session = _Recorder(current=_current_row(project, "预算.xlsx"), flush_fails=True)

    with pytest.raises(ConflictError) as refused:
        await records.replace(
            session, project_id=project, name="预算.xlsx", data=b"v2", by="user-1"
        )

    assert refused.value.status_code == 409
