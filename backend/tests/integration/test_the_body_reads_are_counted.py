"""Memory read counts use complete native Read paths, including sealed records.

Read calls below .cheese/memory/ count by UTC day; MEMORY.md indexes, other
tools and paths outside that directory do not. The time window is half-open.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.block.models import BlockKind
from app.domain.memory.reads import body_reads, is_body_read
from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import project_service, registered, session_auth_headers

# 这个文件里两种都有：域里那几个是 async（`business_db_factory`），接口那两个走
# `client`、是同步的。`anyio_mode = "auto"` 认的是函数本身，所以不打模块级的
# `anyio` 标记 —— 打上它，同步的那两个也会被当成 async，`_pg_schema_gate` 就会去
# 取异步 fixture。

_NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
# 窗口比数据宽一圈：每个用例自己造几个时刻，窗口只负责包住它们。
_SINCE = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
_UNTIL = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
_HOME = "/home/cheese"
_MEMORY = f"{_HOME}/.cheese/memory"

ADMIN = "reads-admin"


async def _project(session, handle: str = "alice"):
    await registered(session, handle)
    project = await project_service(session).create(
        name="正文读数", owner_handle=handle
    )
    await session.flush()
    return project


def _call(
    project,
    *,
    tool: object = "Read",
    detail: object = None,
    arg: object = None,
    at: datetime = _NOW,
    kind: BlockKind = BlockKind.event,
) -> SessionRecord:
    """A native tool call or ordinary text record."""
    sequence = uuid.uuid4().int % (2**62)
    content = (
        [
            {
                "type": "tool_use",
                "name": tool,
                "id": str(sequence),
                "input": {"file_path": detail},
            }
        ]
        if kind == BlockKind.event
        else [{"type": "text", "text": str(detail)}]
    )
    return SessionRecord(
        conversation_id=project.root_topic_id,
        agent_handle="cheese",
        harness="claude_code",
        first_sequence=sequence,
        last_sequence=sequence,
        first_at=at,
        last_at=at,
        count=1,
        bytes=1,
        records=[
            {
                "sequence": sequence,
                "at": at.isoformat(),
                "record": {"type": "assistant", "message": {"content": content}},
            }
        ],
    )


# --- 判据：什么算一次正文读取 -----------------------------------------------


async def test_only_reads_of_body_files_are_counted(business_db_factory):
    async with business_db_factory() as session:
        project = await _project(session)
        session.add_all(
            [
                # 算：读了一条正文。
                _call(project, detail=f"{_MEMORY}/team/release-steps.md"),
                # 不算：索引每轮注入，读它不算翻正文。
                _call(project, detail=f"{_MEMORY}/team/MEMORY.md"),
                # 不算：工具不是 Read。
                _call(project, tool="Write", detail=f"{_MEMORY}/team/x.md"),
                # 不算：别的文件。
                _call(project, detail="/home/cheese/work/wt-dream/CLAUDE.md"),
                # 不算：预览是给人看的短文本（长路径被剪成 `…/team/x.md`），
                # 拿它判目录是不准的 —— 判据必须是未截断的 `detail`。
                _call(project, arg="…/team/x.md"),
                # 不算：普通文本不算工具调用。
                _call(
                    project,
                    detail=f"{_MEMORY}/team/x.md",
                    kind=BlockKind.message,
                ),
            ]
        )
        await session.commit()

        rows = await body_reads(session, since=_SINCE, until=_UNTIL)

        assert [row.as_dict() for row in rows] == [
            {
                "project_id": str(project.id),
                "total": 1,
                "days": [{"day": "2026-09-27", "reads": 1}],
            }
        ]


async def test_the_pure_predicate_and_the_query_agree(business_db_factory):
    """The public path predicate and persisted raw-record counts agree."""
    candidates: list[tuple[object, object]] = [
        ("Read", f"{_MEMORY}/team/a.md"),
        ("Read", f"{_MEMORY}/private/alice/a.md"),
        ("Read", f"{_MEMORY}/team/MEMORY.md"),
        ("Read", f"{_MEMORY}/private/alice/MEMORY.md"),
        ("Read", f"{_MEMORY}/team/MEMORY.md.bak"),
        ("Read", f"{_MEMORY}/team/MEMORY.mdnotes.md"),
        ("Read", f"{_HOME}/.cheese/memory-notes/team/a.md"),
        ("Read", f"{_HOME}/.cheese/memory"),
        ("Read", "/home/cheese/work/CLAUDE.md"),
        ("Read", None),
        ("Write", f"{_MEMORY}/team/a.md"),
        ("Bash", f"{_MEMORY}/team/a.md"),
        (None, f"{_MEMORY}/team/a.md"),
        ("Read", r"C:\Users\cheese\.cheese\memory\team\a.md"),
        ("Read", r"C:\Users\cheese\.cheese\memory\team\MEMORY.md"),
    ]
    async with business_db_factory() as session:
        project = await _project(session)
        moments: list[datetime] = []
        for index, (tool, detail) in enumerate(candidates):
            at = _NOW + timedelta(minutes=10 * index)
            moments.append(at)
            session.add(_call(project, tool=tool, detail=detail, at=at))
        await session.commit()

        for (tool, detail), at in zip(candidates, moments, strict=True):
            rows = await body_reads(
                session,
                since=at,
                until=at + timedelta(minutes=1),
                project_id=project.id,
            )
            counted = bool(rows and rows[0].total)
            assert counted == is_body_read(tool, detail), (tool, detail)


# --- 按项目、按天 -----------------------------------------------------------


async def test_the_count_is_per_project_and_per_day(business_db_factory):
    async with business_db_factory() as session:
        one = await _project(session, "alice")
        two = await _project(session, "bob")
        quiet = await _project(session, "carol")
        session.add_all(
            [
                _call(one, detail=f"{_MEMORY}/team/a.md", at=_NOW),
                _call(one, detail=f"{_MEMORY}/team/b.md", at=_NOW + timedelta(hours=1)),
                _call(
                    one,
                    detail=f"{_MEMORY}/private/alice/a.md",
                    at=_NOW + timedelta(days=2),
                ),
                _call(two, detail=f"{_MEMORY}/team/a.md", at=_NOW + timedelta(days=1)),
            ]
        )
        await session.commit()

        rows = await body_reads(session, since=_SINCE, until=_UNTIL)

        assert [row.project_id for row in rows] == sorted([str(one.id), str(two.id)])
        by_id = {row.project_id: row for row in rows}
        assert by_id[str(one.id)].total == 3
        # 天按 UTC 切、从早到晚：本机的会话时区不是 UTC（见 `reads.body_reads`）。
        assert [(day.day, day.reads) for day in by_id[str(one.id)].days] == [
            ("2026-09-27", 2),
            ("2026-09-29", 1),
        ]
        assert [(day.day, day.reads) for day in by_id[str(two.id)].days] == [
            ("2026-09-28", 1)
        ]
        # 一次正文都没读过的项目不出现 —— 「0」和「不在名单里」是同一件事。
        assert str(quiet.id) not in by_id


async def test_one_project_can_be_asked_for_alone(business_db_factory):
    async with business_db_factory() as session:
        one = await _project(session, "alice")
        two = await _project(session, "bob")
        session.add_all(
            [
                _call(one, detail=f"{_MEMORY}/team/a.md"),
                _call(two, detail=f"{_MEMORY}/team/a.md"),
            ]
        )
        await session.commit()

        rows = await body_reads(session, since=_SINCE, until=_UNTIL, project_id=two.id)

        assert [row.project_id for row in rows] == [str(two.id)]
        assert (
            await body_reads(
                session, since=_SINCE, until=_UNTIL, project_id=uuid.uuid4()
            )
            == []
        )


async def test_the_window_is_half_open(business_db_factory):
    """`since` 那一刻算进来，`until` 那一刻不算：两个窗口首尾相接时不会重复数。"""
    async with business_db_factory() as session:
        project = await _project(session)
        session.add_all(
            [
                _call(project, detail=f"{_MEMORY}/team/a.md", at=_NOW),
                _call(
                    project, detail=f"{_MEMORY}/team/b.md", at=_NOW + timedelta(days=1)
                ),
            ]
        )
        await session.commit()

        first = await body_reads(session, since=_NOW, until=_NOW + timedelta(days=1))
        second = await body_reads(session, since=_NOW + timedelta(days=1), until=_UNTIL)

        assert first[0].total == 1
        assert second[0].total == 1


# --- 接口那一侧 -------------------------------------------------------------


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def test_the_admin_endpoint_answers_per_project_per_day(client, as_admin):
    """管理接口上读到的和域里数是同一份东西：按项目、按天、默认七天。"""
    import asyncio

    async def _seed() -> str:
        async with client.test_factory() as session:
            await registered(session, "reads-owner")
            project = await project_service(session).create(
                name="接口读数", owner_handle="reads-owner"
            )
            await session.flush()
            session.add(
                _call(project, detail=f"{_MEMORY}/team/a.md", at=datetime.now(UTC))
            )
            # 这一条不该进任何一天：读的是索引。
            session.add(
                _call(project, detail=f"{_MEMORY}/team/MEMORY.md", at=datetime.now(UTC))
            )
            await session.commit()
            return str(project.id)

    project_id = asyncio.run(_seed())
    r = client.get("/admin/memory/reads", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text
    body = r.json()["data"]

    assert body["days"] == 7
    row = next(item for item in body["projects"] if item["project_id"] == project_id)
    assert row["total"] == 1
    assert len(row["days"]) == 1
    assert row["days"][0]["reads"] == 1


def test_the_endpoint_needs_to_be_an_admin(client):
    """不是平台管理员的人读不到别人的项目读了些什么。门是 `PlatformAdminDep`。"""
    r = client.get("/admin/memory/reads", headers=session_auth_headers("reads-nobody"))
    assert r.status_code in (401, 403), r.text
