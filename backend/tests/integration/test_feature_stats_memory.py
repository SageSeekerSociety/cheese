"""记忆 的功能数据页：一个项目的记忆树长了什么、干不干净、整理跑到哪了。

这一页要钉的几件事，每一件都是「数错了也看不出来」的那一类：

* **条数只数真的记忆**：索引（``MEMORY.md``）不是一条记忆，被拒的那一版
  （``*.rejected.md``）也不是——它留在目录里给人看，从不进索引，算进去就会变成永远
  清不掉的 orphan。
* **orphan 和 dangling 是两件事**：有文件没进索引是 orphan，索引指着一个不存在的
  文件是 dangling。分开数，因为收拾的办法不一样。
* **「没有索引」不是「索引是空的」**：没有 ``MEMORY.md`` 的项目，行数/字节数是
  ``null`` 而不是 0。
* **人私人记忆只出数、不出名字**：按人聚合，报告里没有文件名、没有正文、没有写了
  什么——只有「这个项目几条、涉及几个人」。
* **整理那一档**：最近一次成功结束的时刻、最近几次的结局（完成 / 失败 / 被防删护栏
  拦下 / 跑着没回音），以及离下一次整理还差多少输出 token。
* **正文读数**复用 ``body_reads`` 的口径：只有 ``Read`` 打到 ``.cheese/memory/`` 下、
  且不是索引的那些。

写数据一律走 ``client`` 那个库（``client.test_factory``），理由写在
``test_admin_stats.py`` 的文件头。
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.models import (
    MemoryDreamRun,
    MemoryDreamRunStatus,
    MemoryDreamState,
    MemoryFileRecord,
)
from app.domain.usage.models import ResourceUsage
from tests.integration.conftest import post_project, session_auth_headers

ADMIN = "memory-stats-admin"
STRANGER = "memory-stats-stranger"

_NOW = datetime.now(UTC).replace(microsecond=0)
_HOME = "/home/cheese"
_MEMORY = f"{_HOME}/.cheese/memory"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _body(name: str, *, text: str = "有结论先说结论。") -> str:
    return f"---\nname: {name}\ndescription: {name}\ntype: feedback\n---\n\n{text}\n"


def _index(*paths: str) -> str:
    return "".join(f"- [{p}]({p}) — hook\n" for p in paths)


def _file(project_id: uuid.UUID, *, path: str, content: str, owner: str = "") -> dict:
    scope = MemoryFileScope.private if owner else MemoryFileScope.project
    return {
        "project_id": project_id,
        "scope": scope,
        "owner_handle": owner,
        "path": path,
        "content": content,
    }


def _seed(client, *rows: object) -> None:
    async def _run() -> None:
        async with client.test_factory() as s:
            s.add_all(rows)
            await s.commit()

    asyncio.run(_run())


def _project(client, name: str = "记忆") -> uuid.UUID:
    return uuid.UUID(_project_out(client, name)["id"])


def _project_out(client, name: str = "记忆") -> dict:
    """The whole ``POST /projects`` payload — some callers want the root topic."""
    resp = post_project(client, json={"name": name}, owner="alice")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _report(client, admin: str, **params) -> dict:
    resp = client.get(
        "/admin/feature-stats/memory",
        params=params,
        headers=session_auth_headers(admin),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _row(report: dict, project_id: uuid.UUID) -> dict:
    found = next(
        (row for row in report["projects"] if row["project_id"] == str(project_id)),
        None,
    )
    assert found is not None, report
    return found


# ---------- 一棵树长什么样 ----------


def test_a_projects_memory_tree_is_counted(client, as_admin):
    """条数、orphan、dangling、超长正文：一棵有毛病的树，每个毛病都数得出。"""
    pid = _project(client)
    _seed(
        client,
        MemoryFileRecord(
            **_file(pid, path=INDEX_NAME, content=_index("a.md", "b.md", "gone.md"))
        ),
        # 两条正常记忆。
        MemoryFileRecord(**_file(pid, path="a.md", content=_body("a"))),
        MemoryFileRecord(**_file(pid, path="b.md", content=_body("b"))),
        # 一条写进去但没进索引（orphan）。
        MemoryFileRecord(**_file(pid, path="c.md", content=_body("c"))),
        # 一条空文件：不算一条记忆。
        MemoryFileRecord(**_file(pid, path="empty.md", content="")),
    )

    row = _row(_report(client, as_admin), pid)
    assert row["entries"] == 3  # a, b, c —— empty 不算
    assert row["orphan"] == 1  # c.md 没进索引
    assert row["dangling"] == 1  # 索引里的 gone.md 不存在
    assert row["over_body"] == 0
    assert row["index"]["lines"] == 3


def test_a_rejected_version_is_not_a_memory(client, as_admin):
    """被拒的那一版既不进条数，也不算 orphan。"""
    pid = _project(client, name="被拒的版本")
    _seed(
        client,
        MemoryFileRecord(**_file(pid, path=INDEX_NAME, content=_index("a.md"))),
        MemoryFileRecord(**_file(pid, path="a.md", content=_body("a"))),
        MemoryFileRecord(
            **_file(pid, path="a.rejected.md", content=_body("a", text="更长的旧版"))
        ),
    )
    row = _row(_report(client, as_admin), pid)
    assert row["entries"] == 1
    assert row["orphan"] == 0


def test_a_project_with_no_index_reports_null_not_zero(client, as_admin):
    """没有索引文件时行数/字节数是 null——「没有索引」不是「索引是空的」。"""
    pid = _project(client, name="没有索引")
    _seed(client, MemoryFileRecord(**_file(pid, path="a.md", content=_body("a"))))
    row = _row(_report(client, as_admin), pid)
    assert row["entries"] == 1
    assert row["index"] == {
        "lines": None,
        "bytes": None,
        "over_lines": False,
        "over_bytes": False,
    }
    assert row["dangling"] == 0  # 没有索引，就没有悬空的行


def test_the_index_is_measured_against_the_injection_budget(client, as_admin):
    """索引行数超过注入上限（200 行）时，这一页数得出来。"""
    pid = _project(client, name="索引超了")
    many = [f"m{i}.md" for i in range(250)]
    _seed(
        client,
        MemoryFileRecord(**_file(pid, path=INDEX_NAME, content=_index(*many))),
    )
    report = _report(client, as_admin)
    row = _row(report, pid)
    assert row["index"]["lines"] == 250
    assert row["index"]["over_lines"] is True
    assert report["numbers"]["index"]["over_lines"] == 1


# ---------- 人的记忆：只出数，不出名字 ----------


def test_personal_memories_are_aggregated_without_names(client, as_admin):
    """按人聚合：两个 owner 一共三条，报告里没有文件名、没有正文。"""
    pid = _project(client, name="私人记忆")
    _seed(
        client,
        MemoryFileRecord(**_file(pid, path="p1.md", content=_body("p1"), owner="bob")),
        MemoryFileRecord(**_file(pid, path="p2.md", content=_body("p2"), owner="bob")),
        MemoryFileRecord(
            **_file(pid, path="p3.md", content=_body("p3"), owner="carol")
        ),
    )
    report = _report(client, as_admin)
    row = _row(report, pid)
    assert row["personal"] == {"entries": 3, "owners": 2}
    assert report["numbers"]["personal"] == {"value": 3, "owners": 2}

    payload = json.dumps(report, ensure_ascii=False)
    assert "p1.md" not in payload
    assert "有结论先说结论" not in payload


# ---------- 整理跑到哪了 ----------


def test_dream_shows_the_last_success_and_the_recent_outcomes(client, as_admin):
    """最近一次成功、最近几次的结局分布、跑着卡住的那一档，以及 token 进度。"""
    pid = _project(client, name="整理")
    long_ago = _NOW - timedelta(hours=20)
    _seed(
        client,
        MemoryDreamState(project_id=pid, last_dream_at=long_ago),
        MemoryDreamRun(
            project_id=pid,
            status=MemoryDreamRunStatus.completed.value,
            started_at=long_ago,
            finished_at=long_ago + timedelta(minutes=5),
        ),
        MemoryDreamRun(
            project_id=pid,
            status=MemoryDreamRunStatus.failed.value,
            started_at=_NOW - timedelta(hours=10),
            finished_at=_NOW - timedelta(hours=10) + timedelta(minutes=2),
        ),
        MemoryDreamRun(
            project_id=pid,
            status=MemoryDreamRunStatus.refused.value,
            started_at=_NOW - timedelta(hours=8),
            finished_at=_NOW - timedelta(hours=8) + timedelta(minutes=1),
        ),
        # 跑着、没回音、起点比一小时还早——算「卡住」。
        MemoryDreamRun(
            project_id=pid,
            status=MemoryDreamRunStatus.running.value,
            started_at=_NOW - timedelta(hours=3),
            finished_at=None,
        ),
        # 上次成功之后花了 2,000,000 输出 token。
        ResourceUsage(project_id=pid, output_tokens=2_000_000, kind="chat"),
        # 整理自己花的不算进去。
        ResourceUsage(
            project_id=pid, output_tokens=5_000_000, kind="memory_dream:turn"
        ),
    )
    row = _row(_report(client, as_admin), pid)
    dream = row["dream"]
    assert dream["last_completed_at"] == long_ago.isoformat()
    assert dream["completed"] == 1
    assert dream["failed"] == 1
    assert dream["refused"] == 1
    assert dream["stuck"] == 1
    assert dream["tokens"] == {
        "value": 2_000_000,
        "threshold": 10_000_000,
    }


# ---------- 正文读数 ----------


def test_the_body_reads_come_from_the_shared_reader(client, as_admin):
    """全平台每天读了多少次正文：只有 Read + 记忆目录 + 不是索引才算。"""
    project = _project_out(client, name="正文读数")
    pid = uuid.UUID(project["id"])
    _seed(
        client,
        *[
            Block(
                project_id=pid,
                topic_id=uuid.UUID(project["root_topic_id"]),
                kind=BlockKind.event,
                author_type=AuthorType.platform,
                author="system",
                content="",
                meta={"tool": tool, "detail": detail},
                created_at=_NOW - timedelta(hours=1),
            )
            for tool, detail in (
                ("Read", f"{_MEMORY}/project/a.md"),
                ("Read", f"{_MEMORY}/project/a.md"),
                ("Read", f"{_MEMORY}/project/{INDEX_NAME}"),  # 索引不算
                ("Write", f"{_MEMORY}/project/a.md"),  # 不是 Read
            )
        ],
    )
    report = _report(client, as_admin, days=7)
    row = _row(report, pid)
    assert row["reads"] == 2
    assert report["numbers"]["reads"]["value"] == 2
    # 折线的最后一天是今天，读数落在今天那一格。
    assert report["trend"][-1]["reads"] == 2


# ---------- 门 ----------


def test_a_stranger_cannot_read_memory_stats(client, as_admin):
    assert (
        client.get(
            "/admin/feature-stats/memory", headers=session_auth_headers(STRANGER)
        ).status_code
        == 403
    )


def test_the_catalogue_lists_memory(client, as_admin):
    body = client.get(
        "/admin/feature-stats", headers=session_auth_headers(as_admin)
    ).json()["data"]
    assert "memory" in [feature["id"] for feature in body["features"]]
