"""管理后台「运行记录」：平台在各个项目里遇到的问题和它自己处理掉的事。

一种事一行：报错按它的指纹认（同一处代码、同一种异常），别的按那句话认（数字抹
掉，「前面还有 2 个」和「前面还有 3 个」是同一种事）。每行说它出了几次、涉及几个
项目、第一次和最近一次是什么时候，再加一条 24 段的小柱图看它是一阵还是一直在。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, Text, Uuid, cast, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.models import Conversation
from app.domain.run_record.models import RunRecord

#: 报错：平台自己的（不属于任何对话），和对话里没跑完的那几轮（搬出对话的历史）。
ERRORS = (
    "backend_error",
    "frontend_error",
    "turn_failed",
    "platform_error",
    "turn_timeout",
)
#: 小柱图分几段。
BUCKETS = 24
WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
#: 项目叫什么：只读这两列，不为它把项目领域拉进来。
_projects = table("projects", column("id", Uuid), column("name", String))


def _key():
    """同一种事的认法，写成 SQL：报错的指纹，否则把数字抹掉的那句话。"""
    said = func.regexp_replace(RunRecord.content, "[0-9]+", "*", "g")
    return func.coalesce(RunRecord.meta["fingerprint"].as_string(), said)


@dataclass
class Group:
    key: str
    kind: str
    severity: str
    title: str
    count: int
    projects: int
    first_at: datetime
    last_at: datetime
    buckets: list[int] = field(default_factory=lambda: [0] * BUCKETS)


def _filters(since: datetime, group: str, query: str | None):
    where = [RunRecord.created_at >= since]
    if group == "errors":
        where.append(RunRecord.kind.in_(ERRORS))
    elif group == "recovered":
        where.append(RunRecord.kind.not_in(ERRORS))
    if query:
        like = f"%{query}%"
        where.append(
            RunRecord.content.ilike(like) | cast(RunRecord.meta, Text).ilike(like)
        )
    return where


async def overview(
    session: AsyncSession,
    *,
    window: str = "24h",
    group: str = "all",
    query: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = now - span
    key = _key().label("key")
    where = _filters(since, group, query)
    rows = (
        await session.execute(
            select(
                key,
                RunRecord.kind,
                func.max(RunRecord.severity),
                func.max(RunRecord.content),
                func.count(),
                func.count(func.distinct(RunRecord.project_id)),
                func.min(RunRecord.created_at),
                func.max(RunRecord.created_at),
            )
            .where(*where)
            .group_by(key, RunRecord.kind)
            .order_by(func.max(RunRecord.created_at).desc())
            .limit(200)
        )
    ).all()
    groups = {
        (k, kind): Group(k, kind, sev, title, n, p, first, last)
        for k, kind, sev, title, n, p, first, last in rows
    }
    step = span / BUCKETS
    bucket = func.floor(
        func.extract("epoch", RunRecord.created_at - since) / step.total_seconds()
    ).label("bucket")
    for k, kind, at, n in (
        await session.execute(
            select(key, RunRecord.kind, bucket, func.count())
            .where(*where)
            .group_by(key, RunRecord.kind, bucket)
        )
    ).all():
        found = groups.get((k, kind))
        if found is not None and 0 <= int(at) < BUCKETS:
            found.buckets[int(at)] = n
    ordered = sorted(
        groups.values(),
        key=lambda g: (g.kind not in ERRORS, -g.last_at.timestamp()),
    )
    errors = [g for g in ordered if g.kind in ERRORS]
    recovered = [g for g in ordered if g.kind not in ERRORS]
    return {
        "window": window,
        "since": since.isoformat(),
        "totals": {
            "error_kinds": len(errors),
            "errors": sum(g.count for g in errors),
            "recovered": sum(g.count for g in recovered),
        },
        "groups": [_out(g) for g in ordered],
    }


def _out(g: Group) -> dict:
    return {
        "key": g.key,
        "kind": g.kind,
        "severity": g.severity,
        "title": g.title,
        "count": g.count,
        "projects": g.projects,
        "first_at": g.first_at.isoformat(),
        "last_at": g.last_at.isoformat(),
        "buckets": g.buckets,
    }


async def detail(
    session: AsyncSession,
    *,
    key: str,
    kind: str,
    window: str = "24h",
    now: datetime | None = None,
) -> dict | None:
    """One kind of thing: its newest record whole, and where it happened."""
    now = now or datetime.now(UTC)
    since = now - WINDOWS.get(window, WINDOWS["24h"])
    match = (
        RunRecord.created_at >= since,
        RunRecord.kind == kind,
        _key() == key,
    )
    newest = await session.scalar(
        select(RunRecord).where(*match).order_by(RunRecord.created_at.desc()).limit(1)
    )
    if newest is None:
        return None
    conversation = func.coalesce(
        RunRecord.conversation_id,
        cast(RunRecord.meta["conversation"].as_string(), Conversation.id.type),
    ).label("conversation")
    places = (
        await session.execute(
            select(
                RunRecord.project_id,
                _projects.c.name,
                conversation,
                func.max(RunRecord.created_at),
            )
            .outerjoin(_projects, _projects.c.id == RunRecord.project_id)
            .where(*match)
            .group_by(RunRecord.project_id, _projects.c.name, conversation)
            .order_by(func.max(RunRecord.created_at).desc())
        )
    ).all()
    return {
        "kind": newest.kind,
        "severity": newest.severity,
        "content": newest.content,
        "meta": newest.meta or {},
        "at": newest.created_at.isoformat(),
        "places": [
            {
                "project_id": str(p) if p else None,
                "project": name,
                "conversation_id": str(c) if c else None,
                "at": at.isoformat(),
            }
            for p, name, c, at in places[:20]
        ],
        "more_places": max(0, len(places) - 20),
    }
