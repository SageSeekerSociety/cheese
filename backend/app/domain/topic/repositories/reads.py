"""频道的读 —— 取行、列清单、数频道。

从 `repositories.py` 拆出来的一段。这里最要紧的一件东西是 `_project_topics_stmt`：
「这些项目的频道树，平铺，按这个次序」只有那一份定义，`list_for_project`、
`ids_for_project`、`names_in_projects`、`list_for_project_with_activity` 都从它出发，
所以同一个清单不会在两处被筛得或排得不一样。

`TopicRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Uuid, any_, bindparam, column, func, select, table
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.core.request_facts import recall, remember
from app.domain.topic.models import Topic, TopicStatus, not_a_private_chat
from app.domain.topic.repositories.statements import (
    _last_activity,
    _order_by,
    _tasks,
    seen_by,
)
from app.domain.topic.repositories.vocabulary import (
    ListedRoom,
    SortOrder,
    TopicSortField,
)

if TYPE_CHECKING:
    from app.domain.topic import repositories as pkg


_TOPIC_ROW_TABLES = frozenset({"topics"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


async def get(repo: pkg.TopicRepository, topic_id: uuid.UUID) -> Topic | None:
    if not _can_cache(repo._session):
        return await repo._session.get(Topic, topic_id)
    if repo._session.autoflush:
        await repo._session.flush()
    key = ("topic_row", topic_id)
    found, cached = recall(repo._session, key)
    if found:
        return cached
    topic = await repo._session.get(Topic, topic_id)
    remember(repo._session, key, topic, tables=_TOPIC_ROW_TABLES)
    return topic


async def get_many(repo: pkg.TopicRepository, topic_ids: set[uuid.UUID]) -> list[Topic]:
    if not topic_ids:
        return []
    if not _can_cache(repo._session):
        return list(
            await repo._session.scalars(select(Topic).where(Topic.id.in_(topic_ids)))
        )
    if repo._session.autoflush:
        await repo._session.flush()
    known: list[Topic] = []
    missing: list[uuid.UUID] = []
    for tid in topic_ids:
        found, cached = recall(repo._session, ("topic_row", tid))
        if found:
            if cached is not None:
                known.append(cached)
        else:
            missing.append(tid)
    if not missing:
        return known
    rows = list(await repo._session.scalars(select(Topic).where(Topic.id.in_(missing))))
    fetched = {t.id: t for t in rows}
    for tid in missing:
        t = fetched.get(tid)
        if t is not None:
            known.append(t)
        remember(repo._session, ("topic_row", tid), t, tables=_TOPIC_ROW_TABLES)
    return known


async def list_for_project(
    repo: pkg.TopicRepository,
    project_id: uuid.UUID,
    *,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
    only_seen_by: str | None = None,
) -> list[Topic]:
    """The project's topic tree, flat: all of it, or what ``only_seen_by``
    sees when one is named (``seen_by``).

    ``active_since`` keeps only topics whose last activity (see
    ``_last_activity``) is at or after that instant — "最近活跃的话题". It
    filters the flat list, so a kept topic's parent may be filtered out;
    callers that rebuild the tree should not combine it with the filter.
    """
    cache_all = (
        _can_cache(repo._session)
        and sort is None
        and order == "asc"
        and active_since is None
        and only_seen_by is None
    )
    if cache_all:
        if repo._session.autoflush:
            await repo._session.flush()
        found, cached = recall(repo._session, ("project_topics_all", project_id))
        if found:
            return list(cached)
    stmt = _project_topics_stmt(
        repo,
        [project_id],
        sort=sort,
        order=order,
        active_since=active_since,
        seen=None if only_seen_by is None else seen_by(only_seen_by),
    )
    rows = list((await repo._session.scalars(stmt)).all())
    if _can_cache(repo._session):
        if cache_all:
            remember(
                repo._session,
                ("project_topics_all", project_id),
                tuple(rows),
                tables=_TOPIC_ROW_TABLES,
            )
        for row in rows:
            remember(
                repo._session, ("topic_row", row.id), row, tables=_TOPIC_ROW_TABLES
            )
    return rows


async def ids_for_project(
    repo: pkg.TopicRepository,
    project_id: uuid.UUID,
    *,
    only_seen_by: str | None = None,
    unarchived: bool = False,
) -> list[uuid.UUID]:
    """同一棵树，只要主键 —— 调用方问的是「哪些频道算数」，不读那一行。

    项目任务列表要的正是这个：它把「看得见的房间」当筛选条件交给数据库
    （`room_task.ListedTask` 那边同理），原来却为拿这几个 id 装配了整个项目
    的频道行 —— 219 行里除了 id 一个字段都不读，而 `Topic` 一行还背着环境
    文档那两列 JSON（2026-10-15，48 万区块的库，一次项目任务列表）。

    `unarchived` 排掉归档的频道：它们是只读的，里面没有还能推进的活。
    走的还是 `_project_topics_stmt`，所以筛选和排序只有那一份定义。
    """
    cache_all = _can_cache(repo._session) and only_seen_by is None and not unarchived
    if cache_all:
        if repo._session.autoflush:
            await repo._session.flush()
        found_topics, cached_topics = recall(
            repo._session, ("project_topics_all", project_id)
        )
        if found_topics:
            return [t.id for t in cached_topics]
        found_ids, cached_ids = recall(
            repo._session, ("project_topic_ids_all", project_id)
        )
        if found_ids:
            return list(cached_ids)
    stmt = _project_topics_stmt(
        repo,
        [project_id],
        sort=None,
        order="asc",
        active_since=None,
        seen=None if only_seen_by is None else seen_by(only_seen_by),
    ).with_only_columns(Topic.id)
    if unarchived:
        stmt = stmt.where(Topic.status != TopicStatus.archived)
    ids = list((await repo._session.scalars(stmt)).all())
    if cache_all:
        remember(
            repo._session,
            ("project_topic_ids_all", project_id),
            tuple(ids),
            tables=_TOPIC_ROW_TABLES,
        )
    return ids


async def visible_unarchived_ids_for_projects(
    repo: pkg.TopicRepository, project_ids: list[uuid.UUID], *, viewer: str
) -> list[uuid.UUID]:
    """同一棵树，跨若干个项目 —— 「待我处理」要问的是我能看见的全部项目，和
    其中 ``viewer`` 看得见、**没归档**的频道（私密频道只有在里面的人看得见）。
    只回主键。

    「待我处理」拿它要的是「哪些频道算数」—— 归档的频道是只读的，里面没有人
    还能推进的事，所以它的活和提问都不进清单。这一笔因此直接问「算数的频道有
    哪几个」，调用方不必再把状态拿回去自己按词比一遍（那条词表是 `topic` 领域
    的，路由不该认它）。一整屏只点到一两个人，却要为全部频道付整行装配的钱
    （量出来 240 行 5 毫秒，而摆上去的常常只有一两行），所以整行留给真正要摆的
    那几个，见 `list_by_ids`。

    私聊照样不在里面（见 `_project_topics_stmt`）：它不是话题树的一部分，也从
    来不会有验收卡或者待确认问题挂在上面。
    """
    if not project_ids:
        return []
    stmt = select(Topic.id).where(
        Topic.project_id.in_(project_ids),
        not_a_private_chat(),
        Topic.deleted_at.is_(None),
        Topic.status != TopicStatus.archived,
        seen_by(viewer),
    )
    return list((await repo._session.scalars(stmt)).all())


async def list_by_ids(
    repo: pkg.TopicRepository, topic_ids: Collection[uuid.UUID]
) -> list[Topic]:
    """这些频道的整行。点不到的 id 就是没有这一行，不是错误。"""
    ids = list(topic_ids)
    if not ids:
        return []
    stmt = select(Topic).where(
        Topic.id == any_(bindparam(None, ids, type_=ARRAY(Uuid)))
    )
    return list((await repo._session.scalars(stmt)).all())


async def listed_by_ids(
    repo: pkg.TopicRepository, topic_ids: Collection[uuid.UUID]
) -> list[ListedRoom]:
    """这些频道摆上清单时要读的那几列。

    一列投影而不是整行：`Topic` 背着环境文档（`environment` /
    `default_environment` 两列 JSON）和简介，摆一屏清单一个字都不读。整行留给真要
    那份正文的地方，见 `list_by_ids`。点不到的 id 就是没有这一行，不是错误。

    id 以同一个数组参数进去（同 `list_by_ids`）：几百个 id 拼成 `IN` 列表会让驱动
    每次重拼一遍语句。
    """
    ids = list(topic_ids)
    if not ids:
        return []
    stmt = select(
        Topic.id,
        Topic.project_id,
        Topic.title,
        Topic.status,
        Topic.accepted_at,
        Topic.updated_at,
    ).where(Topic.id == any_(bindparam(None, ids, type_=ARRAY(Uuid))))
    return [
        ListedRoom(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            status=row.status,
            accepted_at=row.accepted_at,
            updated_at=row.updated_at,
        )
        for row in (await repo._session.execute(stmt)).all()
    ]


def _project_topics_stmt(
    repo: pkg.TopicRepository,
    project_ids: list[uuid.UUID],
    *,
    sort: TopicSortField | None,
    order: SortOrder,
    active_since: datetime | None,
    seen: ColumnElement[bool] | None = None,
) -> Select[tuple[Topic]]:
    """The one definition of "these projects' topic trees, flat, in order".

    Shared so ``list_for_project``, ``list_for_project_with_activity`` and
    ``names_in_projects`` cannot drift into filtering or ordering the same
    list differently. ``seen`` narrows it to what one person sees
    (``seen_by``); without it every channel is listed, for the platform's
    own use.
    """
    # Private chats are not part of the topic tree. A deleted channel is in
    # none of them: it is on its way out (`deleted_at`), and it disappears
    # from every list the moment it is deleted.
    stmt = select(Topic).where(
        Topic.project_id.in_(project_ids),
        not_a_private_chat(),
        Topic.deleted_at.is_(None),
    )
    if seen is not None:
        stmt = stmt.where(seen)
    if active_since is not None:
        stmt = stmt.where(_last_activity() >= active_since)
    return stmt.order_by(_order_by(sort, order))


async def names_in_projects(
    repo: pkg.TopicRepository, project_ids: list[uuid.UUID], *, viewer: str
) -> list[Topic]:
    """Every topic of these projects ``viewer`` sees, private chats left
    out, newest activity first — the rows the sidebar would list, for a name
    search across them."""
    if not project_ids:
        return []
    stmt = _project_topics_stmt(
        repo,
        project_ids,
        sort="last_activity_at",
        order="desc",
        active_since=None,
        seen=seen_by(viewer),
    ).options(defer(Topic.environment), defer(Topic.default_environment))
    return list(await repo._session.scalars(stmt))


async def list_for_project_with_activity(
    repo: pkg.TopicRepository,
    project_id: uuid.UUID,
    *,
    viewer: str | None,
    sort: TopicSortField | None = None,
    order: SortOrder = "asc",
    active_since: datetime | None = None,
) -> list[tuple[Topic, datetime]]:
    """The same list, each row paired with its 最后活动时间 — in ONE query.

    The list endpoint needs both, and asking for them separately made the
    database derive ``_last_activity`` twice over the same topics: once to
    sort by it, once to report it. Selecting it alongside the rows it
    already sorted costs nothing extra, because it is the expression the
    ORDER BY evaluates anyway.

    Separate from ``list_for_project`` rather than replacing it: that one's
    ``list[Topic]`` is what mentions, the agent's context builders and the
    dashboard want, and none of them look at last activity.

    ``Topic.environment`` and ``Topic.default_environment`` are deferred:
    the list endpoint (``TopicOut``) and ``readable_rooms`` never read sandbox
    environment documents, so pulling and deserialising both JSON columns on
    every row is wasted work. Callers that need those columns must load a
    full row (``get`` or ``list_for_project``).
    """
    stmt = (
        _project_topics_stmt(
            repo,
            [project_id],
            sort=sort,
            order=order,
            active_since=active_since,
            seen=seen_by(viewer),
        )
        .options(defer(Topic.environment), defer(Topic.default_environment))
        .add_columns(_last_activity())
    )
    rows = (await repo._session.execute(stmt)).all()
    return [(topic, last) for topic, last in rows]


async def last_activity_for_topics(
    repo: pkg.TopicRepository, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """{topic_id: last activity} for a batch of topics, in ONE query.

    For callers holding topics they did not get from
    ``list_for_project_with_activity`` — a single room's header opened by
    deep link, which has one topic and no list to have derived it with.
    A caller that is about to list a project's topics should use that
    method instead and get both from one query.
    """
    if not topic_ids:
        return {}
    stmt = select(Topic.id, _last_activity()).where(Topic.id.in_(topic_ids))
    rows = (await repo._session.execute(stmt)).all()
    return {topic_id: last for topic_id, last in rows}


async def list_children(repo: pkg.TopicRepository, parent_id: uuid.UUID) -> list[Topic]:
    stmt = select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.created_at)
    return list((await repo._session.scalars(stmt)).all())


async def archival_for_project(
    repo: pkg.TopicRepository, project_id: uuid.UUID
) -> dict[uuid.UUID, datetime | None]:
    """Every topic of the project — private chats included, since they have
    directories too — mapped to when it was archived (None while active).
    What the storage sweep reconciles the disk against."""
    stmt = select(Topic.id, Topic.archived_at).where(Topic.project_id == project_id)
    return dict((await repo._session.execute(stmt)).tuples().all())


async def has_open_tasks(repo: pkg.TopicRepository, room_id: uuid.UUID) -> bool:
    """这间房里还有没有没结束的活。

    归档和删除都要在这上面挡一下：房间关掉会把它里面的活一起结束，而那是活主
    人的东西，平台不替他们关。`status` 就 `open` 和 `closed` 两个值
    （`room_task.models.TaskStatus`），所以「有没有 `open`」和「有没有没结束的」
    是同一句话。

    走裸表 `_tasks`：`tasks` 是 `room_task` 的行，本包是向下的那一边读不到它的
    模型（见 `statements.py`）。这里只看 `room_id` 和 `status` 两列，一行也不装
    配 —— 调用方问的是「有没有」，不是「哪几条」。
    """
    stmt = (
        select(func.count())
        .select_from(_tasks)
        .where(_tasks.c.room_id == room_id, _tasks.c.status == "open")
    )
    return bool((await repo._session.scalar(stmt)) or 0)


async def count_for_project(
    repo: pkg.TopicRepository, project_id: uuid.UUID, *, viewer: str | None
) -> int:
    stmt = (
        select(func.count())
        .select_from(Topic)
        .where(
            Topic.project_id == project_id,
            not_a_private_chat(),
            Topic.deleted_at.is_(None),
            seen_by(viewer),
        )
    )
    return int((await repo._session.scalar(stmt)) or 0)


async def task_conversations_in(
    repo: pkg.TopicRepository, room_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Every task in these rooms, open or closed, and the 支线 each was made
    from: the conversations archiving them may say something in (a task's
    change says how it went where it came from too). 走裸表，理由同
    `has_open_tasks`。"""
    if not room_ids:
        return []
    tasks = table(
        "tasks",
        column("id", Uuid),
        column("room_id", Uuid),
        column("upgraded_from_block_id", Uuid),
    )
    threads = table("threads", column("id", Uuid), column("root_block_id", Uuid))
    rows = (
        await repo._session.execute(
            select(tasks.c.id, threads.c.id)
            .select_from(tasks)
            .outerjoin(
                threads, threads.c.root_block_id == tasks.c.upgraded_from_block_id
            )
            .where(tasks.c.room_id.in_(room_ids))
        )
    ).all()
    return [one for row in rows for one in row if one is not None]
