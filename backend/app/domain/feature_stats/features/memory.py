"""记忆 的数据页: 一个项目的记忆树有多大、干不干净、整理跑到哪了。

后台「功能数据」里的一页，回答的是「这套文件式记忆在一个项目里长成了什么样」：写了
多少条、索引有没有撑破注入预算、有没有谁写了却没进索引的文件（或者索引指着一个不存在
的文件）、正文是不是太长、整理（dream）最近跑得成不成、这一轮离下一次整理还差多少。

**只数数，不读内容。** 这一页唯一的取数是 `memory_files` 的路径/作用域/正文长度和
`MEMORY.md` 的索引行——记忆正文本身、private 记忆的文件名一个字都不出现在报告里。人
的记忆按人聚合，报告里只有「这个项目有几条、涉及几个人」，没有是谁写了什么。隐私底线
和「问芝士」那一页是同一条（见 `docs/manual/dev/feature-stats.md`）。

三个来源，都不新增埋点：

* ``memory_files`` —— 真相那张表。每一个作用域一份索引（`MEMORY.md`）加若干条记忆，
  都在这里。这一页把它按项目读回来，在进程里数：条数、orphan、dangling、超长正文。
* ``memory_dream_runs`` / ``memory_dream_states`` —— 整理那两本账：成功结束的时刻、
  最近几次的结局（完成 / 失败 / 被防删护栏拦下 / 跑了很久没回音）。
* ``blocks`` —— 正文到底被读过几次，复用 `domain/memory/reads.body_reads`（和
  ``GET /admin/memory/reads`` 同一个函数，口径只有一处）。

**「没读到」是 ``null``。** 一个项目没有索引文件时，它的行数/字节数是 ``null`` 而不是
0——「没有索引」和「索引是空的」是两句话。整理从没成功过时 ``last_completed_at`` 也是
``null``。

一个项目一次输出 token 的进度要单独问一次库（阈值的时间原点按项目的
``last_dream_at`` 走，各不相同），所以这一页的取数随项目数线性增长。它是给运营偶尔
打开一次的，不是高频接口——和 `feature_stats` 里别的页同一个量级。
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import dream
from app.domain.memory.files import (
    BODY_MAX,
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    INDEX_NAME,
    MemoryFileError,
    MemoryFileScope,
    fit_index,
    parse_index,
    parse_memory_file,
)
from app.domain.memory.models import (
    MemoryDreamRun,
    MemoryDreamRunStatus,
    MemoryDreamState,
    MemoryFileRecord,
)
from app.domain.memory.reads import body_reads
from app.domain.platform_stats.windows import dense_series, utc_day_window
from app.domain.project.models import Project
from app.domain.usage.models import ResourceUsage

FEATURE_ID = "memory"
TITLE = "记忆"
SUMMARY = "文件式记忆：一个项目写了多少、索引撑没撑破、整理跑到哪了"

#: 一种 special 的文件名后缀：被拒的那一版（`files.rejected_path`）。它留在同一
#: 个目录里给人看，但从不是一条记忆——没过 `check_path`，对账从不当它是记忆收回去，
#: 也永远不进索引。所以它既不算「条数」，也不算 orphan（否则每个被拒的版本都会变成
#: 一个永远清不掉的 orphan）。
_REJECTED_SUFFIX = ".rejected.md"

#: 最近几次整理拿来算结局分布。「最近 N 次」是这一页的口径：整理一次可能几分钟，
#: 一个项目一周也跑不了几次，十次足够看出「最近是不是一直在失败」。
RECENT_RUNS = 10

#: 跑着还没回音的整理，超过这么久算「卡住」。取一小时：一次整理正常是几分钟，
#: 一小时还没结束多半是那台机器没了——不是 `CLAIM_TTL` 那把锁的过期（那是 6 小时，
#: 管的是「谁可以重新占锁」），是给人看的「这个看着不对劲」。
STUCK_AFTER = timedelta(hours=1)


def _is_memory_file(path: str) -> bool:
    """这个路径算不算「一条记忆」：不是索引，也不是被拒的那一版。"""
    return path != INDEX_NAME and not path.endswith(_REJECTED_SUFFIX)


def _body_length(content: str) -> int:
    """一条记忆正文的长度。读不成记忆文件就按整份算——和 `limit_breach` 一样，
    frontmatter 写错不是绕过上限的办法。"""
    try:
        return len(parse_memory_file(content).body)
    except MemoryFileError:
        return len(content.strip())


def _index_shape(content: str | None) -> dict:
    """一份索引的行数、字节数，以及它撑没撑破注入预算。

    判「超了没有」直接问 `fit_index`：它就是注入时真正用的那一份规则，这里抄一遍
    迟早会走散。行数/字节数只是画出来给人看的两列。没有索引文件时三个都是 ``null``
    / ``False``——「没有索引」不是「索引是空的」。
    """
    if content is None:
        return {"lines": None, "bytes": None, "over_lines": False, "over_bytes": False}
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    size = len(content.encode())
    over = fit_index(content)[1] is not None
    return {
        "lines": lines,
        "bytes": size,
        "over_lines": over and lines > INDEX_MAX_LINES,
        "over_bytes": over and size > INDEX_MAX_BYTES,
    }


def _scope_shape(files: list[MemoryFileRecord]) -> dict:
    """一个作用域里的文件读成这一页要的四个数：条数、orphan、dangling、超长正文。

    ``files`` 是这个作用域在库里的全部行（含索引本身）。索引指向的文件集合从
    `parse_index` 拿；两边对不上的就是 orphan（有文件没进索引）和 dangling（索引
    指着不存在的文件）。被拒的那一版两头都不算——它本来就不该被索引收下。

    **空文件也不算一条记忆**：它既不进条数，也不进 orphan（一个空文件不是「写了却
    没进索引」，它根本就没写）；但索引指向一个**存在**的空文件不算 dangling——那
    一行指的文件确实在，只是里面没东西。所以 orphan 只数非空的条目，dangling 只
    看文件在不在。
    """
    entries = {row.path: row.content for row in files if _is_memory_file(row.path)}
    existing = set(entries)  # 文件在不在（空也算在，用来判 dangling）
    real = {path for path, text in entries.items() if text.strip()}  # 真的是一条记忆
    index = next((row.content for row in files if row.path == INDEX_NAME), None)
    referenced = (
        {entry.path for entry in parse_index(index)} if index is not None else set()
    )
    return {
        "entries": len(real),
        "orphan": sum(1 for path in real if path not in referenced),
        "dangling": sum(1 for path in referenced if path not in existing),
        "over_body": sum(
            1
            for path, text in entries.items()
            if path in real and _body_length(text) > BODY_MAX
        ),
        "index": _index_shape(index),
    }


def _personal_shape(files: list[MemoryFileRecord]) -> dict:
    """人私人记忆按人聚合：**只有条数，没有内容、没有文件名**。

    同一个人的记忆可能有多条，报告里按 owner_handle 去重成「涉及几个人」。这是这一
    页唯一碰 private 的地方，而它连文件名都不看。
    """
    per_owner: dict[str, int] = {}
    for row in files:
        if not _is_memory_file(row.path):
            continue
        if not row.content.strip():
            continue
        per_owner[row.owner_handle] = per_owner.get(row.owner_handle, 0) + 1
    return {"entries": sum(per_owner.values()), "owners": len(per_owner)}


def _dream_shape(runs: list[MemoryDreamRun], *, now: datetime) -> dict:
    """最近几次整理的结局分布，加上「跑着没回音」的那一档。

    ``stuck`` 不在 ``status`` 里：库里那一行还是 ``running``，有没有卡住是**现在**
    才判得出来的（`finished_at` 为空、且起点已经超过 `STUCK_AFTER`）。它和另外三档
    并列画——读的人要看出的是「最近十次里有几次像这样挂在半路」。
    """
    counts = {status.value: 0 for status in MemoryDreamRunStatus}
    stuck = 0
    for run in runs:
        if run.status in counts:
            counts[run.status] += 1
        if (
            run.status == MemoryDreamRunStatus.running.value
            and run.finished_at is None
            and now - run.started_at > STUCK_AFTER
        ):
            stuck += 1
    return {
        "completed": counts[MemoryDreamRunStatus.completed.value],
        "failed": counts[MemoryDreamRunStatus.failed.value],
        "refused": counts[MemoryDreamRunStatus.refused.value],
        "running": counts[MemoryDreamRunStatus.running.value],
        "stuck": stuck,
    }


async def _files(session: AsyncSession) -> dict[str, list[MemoryFileRecord]]:
    """全部记忆文件，按项目分好组。一次读回来在进程里分——这张表是「一个项目几十条」
    的量级，按项目各查一次反而更多来回。"""
    rows = (await session.execute(select(MemoryFileRecord))).scalars().all()
    grouped: dict[str, list[MemoryFileRecord]] = {}
    for row in rows:
        grouped.setdefault(str(row.project_id), []).append(row)
    return grouped


async def _recent_runs(session: AsyncSession) -> dict[str, list[MemoryDreamRun]]:
    """每个项目最近 ``RECENT_RUNS`` 次整理，新的在前。

    在进程里按项目截断（行数在海量项目之前都不大），不写窗口函数——理由是这一页
    读一次就够，SQL 里那点省下的内存不值得多一段认不出的查询。
    """
    rows = (
        (
            await session.execute(
                select(MemoryDreamRun).order_by(MemoryDreamRun.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[MemoryDreamRun]] = {}
    for run in rows:
        bucket = grouped.setdefault(str(run.project_id), [])
        if len(bucket) < RECENT_RUNS:
            bucket.append(run)
    return grouped


async def _states(session: AsyncSession) -> dict[str, MemoryDreamState]:
    rows = (await session.execute(select(MemoryDreamState))).scalars().all()
    return {str(row.project_id): row for row in rows}


async def _tokens_since(session: AsyncSession, project_id: str, since: datetime) -> int:
    """``since`` 之后这个项目花了多少输出 token，不含整理自己那些。

    和 `agent.memory_ledger._output_tokens_since` 是同一条查询、同一个口径：整理
    的触发阈值量的是「这个项目最近写了多少」，按 ``kind`` 前缀把整理那一轮自己花
    的剔掉。这里自己写一遍而不是跨域 import 那个私有函数——它属于 agent 那一簇，
    不是给功能页用的库。
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(ResourceUsage.output_tokens), 0)).where(
            ResourceUsage.project_id == project_id,
            ResourceUsage.created_at > since,
            ResourceUsage.kind.notlike(f"{dream.DREAM_KIND}%"),
        )
    )
    return int(total or 0)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _distinct_owners(files: Iterable[MemoryFileRecord]) -> int:
    """全部项目合起来，有多少个不同的人有私人记忆。跨项目去重，不是各项目加起来。"""
    return len(
        {
            row.owner_handle
            for row in files
            if row.scope == MemoryFileScope.private
            and _is_memory_file(row.path)
            and row.content.strip()
        }
    )


async def load(session: AsyncSession, *, days: int) -> dict[str, Any]:
    """The whole page for one window. See the module docstring for the shape."""
    since, until, buckets = utc_day_window(days)
    now = datetime.now(UTC)

    files = await _files(session)
    runs = await _recent_runs(session)
    states = await _states(session)
    reads = await body_reads(session, since=since, until=until)

    # 一个项目出现在这一页上，当它写过记忆、整理过、或者有人读过正文。
    project_ids = (
        set(files) | set(runs) | set(states) | {row.project_id for row in reads}
    )
    known = (
        (
            await session.execute(
                select(Project.id, Project.name, Project.settings).where(
                    Project.id.in_({row_id for row_id in project_ids})
                )
            )
        ).all()
        if project_ids
        else []
    )
    names = {str(row[0]): row[1] for row in known}
    settings = {str(row[0]): row[2] for row in known}
    reads_by_project = {row.project_id: row.total for row in reads}

    all_files = [row for group in files.values() for row in group]
    rows: list[dict] = []
    for project_id in project_ids:
        group = files.get(project_id, [])
        shared = [row for row in group if row.scope == MemoryFileScope.project]
        private = [row for row in group if row.scope == MemoryFileScope.private]
        shared_shape = _scope_shape(shared)
        state = states.get(project_id)
        config = dream.dream_settings(settings.get(project_id))
        origin = dream.since_of(state)
        tokens = await _tokens_since(session, project_id, origin)
        rows.append(
            {
                "project_id": project_id,
                "name": names.get(project_id, ""),
                "entries": shared_shape["entries"],
                "personal": _personal_shape(private),
                "index": shared_shape["index"],
                "orphan": shared_shape["orphan"],
                "dangling": shared_shape["dangling"],
                "over_body": shared_shape["over_body"],
                "reads": reads_by_project.get(project_id, 0),
                "dream": {
                    "last_completed_at": _iso(
                        state.last_dream_at if state is not None else None
                    ),
                    **_dream_shape(runs.get(project_id, []), now=now),
                    "tokens": {
                        "value": tokens,
                        "threshold": config.threshold_output_tokens,
                    },
                },
            }
        )
    rows.sort(key=lambda row: row["entries"] + row["personal"]["entries"], reverse=True)

    by_day: dict[date, int] = {}
    for read in reads:
        for item in read.days:
            day = date.fromisoformat(item.day)
            by_day[day] = by_day.get(day, 0) + item.reads

    last_completed = [
        moment
        for moment in (row["dream"]["last_completed_at"] for row in rows)
        if moment is not None
    ]
    return {
        "id": FEATURE_ID,
        "title": TITLE,
        "summary": SUMMARY,
        "days": days,
        "start": buckets[0].isoformat(),
        "end": buckets[-1].isoformat(),
        "numbers": {
            "projects": {"value": len(rows)},
            "entries": {"value": sum(row["entries"] for row in rows)},
            "personal": {
                "value": sum(row["personal"]["entries"] for row in rows),
                "owners": _distinct_owners(all_files),
            },
            "index": {
                # 有几个项目的索引撑破了注入预算。`over` 是**按项目去重**的那个数
                # （行数超了或字节超了都算），两种口径再分开数：行数超了和字节超了
                # 是两种不同的收拾办法，合成一个数读不出该往哪儿改。
                "over": sum(
                    1
                    for row in rows
                    if row["index"]["over_lines"] or row["index"]["over_bytes"]
                ),
                "over_lines": sum(1 for row in rows if row["index"]["over_lines"]),
                "over_bytes": sum(1 for row in rows if row["index"]["over_bytes"]),
            },
            "hygiene": {
                "orphan": sum(row["orphan"] for row in rows),
                "dangling": sum(row["dangling"] for row in rows),
                "over_body": sum(row["over_body"] for row in rows),
            },
            "reads": {"value": sum(row["reads"] for row in rows)},
            "dream": {
                "last_completed_at": max(last_completed) if last_completed else None,
                "completed": sum(row["dream"]["completed"] for row in rows),
                "failed": sum(row["dream"]["failed"] for row in rows),
                "refused": sum(row["dream"]["refused"] for row in rows),
                "running": sum(row["dream"]["running"] for row in rows),
                "stuck": sum(row["dream"]["stuck"] for row in rows),
            },
        },
        "projects": rows,
        # 一天一条线：全平台每天读了多少次记忆正文。缺的那天由 `dense_series` 按窗口
        # 补 0，不是按「有数据的那些天」——安静的一天画成断线会被读成「没统计」。
        "trend": dense_series(buckets, {"reads": by_day}),
    }
