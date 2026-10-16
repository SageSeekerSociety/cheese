"""反馈的读：单条、列表、时间线、备注、已读状态。

看见什么由 `services/` 的判据决定，这里只把那些条件执行掉 —— 列表怎么筛、游标
怎么走、热门怎么排序，全在 `queries.py` 里写成条件表达式，这一层拿一条 session
把它们交给数据库。

`FeedbackRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, and_, func, or_, select

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackNote,
    FeedbackReadState,
    FeedbackStatus,
    FeedbackTimeline,
    FeedbackVisibility,
)
from app.domain.feedback.queries import (
    PUBLIC_ONLY,
    filed_in_a_room_of,
    live_comment_clause,
    matching,
    visible_to,
)

if TYPE_CHECKING:
    from app.domain.feedback import repositories as pkg


async def get(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID
) -> Feedback | None:
    stmt: Select[tuple[Feedback]] = select(Feedback).where(
        Feedback.id == feedback_id, Feedback.deleted_at.is_(None)
    )
    return (await repository._session.execute(stmt)).scalar_one_or_none()


async def filed_in_a_room_of_mine(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID, handle: str
) -> bool:
    """:func:`filed_in_a_room_of`, asked about one row.

    `may_see` runs the very clause the list query runs, against this one id,
    instead of re-deciding the rule in Python: the answer needs the roster,
    the roster is a table, and a second spelling of a visibility rule is what
    `PUBLIC_ONLY` and `visible_to` both exist to prevent.

    「当时在不在那个房间里」 only. 「今天还读得到那个房间」 is the other half
    and stays in `may_see`.
    """
    stmt = select(Feedback.id).where(
        Feedback.id == feedback_id, filed_in_a_room_of(handle)
    )
    return (await repository._session.scalar(stmt)) is not None


async def list_public(
    repository: pkg.FeedbackRepository,
    *,
    tab: str,
    q: str | None,
    sort: str,
    limit: int,
    offset: int,
    author: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
) -> tuple[list[Feedback], int]:
    # Everything that narrows the tab **except** the hot rule itself. The hot
    # rule is then expressed against this list rather than appended to it —
    # `where.append(x(_hot_ids(where)))` reads as if the subquery could see the
    # append, and the next person to reorder these two lines would make that
    # true. `q` is part of it too: searching inside 「热门」 asks 「这一栏里哪些
    # 命中」，而补足的那几条也必须来自同一批命中，否则搜完之后这一栏又会多出几条
    # 不匹配的行。
    where: list[Any] = list(PUBLIC_ONLY)
    where.extend(repository._tab_where(tab))
    if q:
        where.append(matching(q))
    # 四个筛选**加在栏位之上**，不替换它：它们是同一批行的进一步收窄
    # （`tab` 说的是「哪一栏」，这四个说的是「那一栏里哪些」）。全部走等值比较
    # 与 `>=`，所以都能吃到 `ix_feedback_visibility_status_created` 那一组索引的
    # 前缀；`author` 另有 `ix_feedback_author_created`。
    if author is not None:
        where.append(Feedback.author_handle == author)
    if status is not None:
        where.append(Feedback.status == status)
    if kind is not None:
        where.append(Feedback.kind == kind)
    if since is not None:
        where.append(Feedback.created_at >= since)
    if tab == "hot":
        # `hot` is a filter **and** an ordering, and both come from
        # `hot_score()` — the tab's definition, not just its sort.
        where.append(Feedback.id.in_(repository._hot_ids(where)))
        sort = "hot"
    # No membership test here: `_list_stmt` is total (anything that is not
    # `supports` is newest-first), so the vocabulary lives in one place —
    # `services.SORTS`. The public route keeps accepting an unknown sort
    # silently; the admin one refuses it, because there it is a control the
    # client draws and a stale client would render the wrong ordering under
    # the right heading. See `services.list_admin`.
    rows = list(
        (
            await repository._session.execute(
                repository._list_stmt(
                    where=where, sort=sort, limit=limit, offset=offset, q=q
                )
            )
        )
        .scalars()
        .all()
    )
    return rows, await repository._count(where)


async def list_admin(
    repository: pkg.FeedbackRepository,
    *,
    tab: str,
    assignee: str | None,
    q: str | None,
    sort: str = "new",
    limit: int,
    offset: int,
    since: datetime | None = None,
    resolved_since: datetime | None = None,
    deployed_since: datetime | None = None,
) -> tuple[list[Feedback], int]:
    where: list[Any] = []
    if tab == "private":
        # 私密非安全: the column an admin works through when the reporter
        # asked for it not to be public but it has no security implication.
        where.append(Feedback.visibility == FeedbackVisibility.private)
        where.append(Feedback.security.is_(False))
    elif tab == "agent":
        where.append(Feedback.author_is_agent.is_(True))
    elif tab == "security":
        where.append(Feedback.security.is_(True))
    elif tab == "public":
        # The same predicate `list_public` uses, so the admin's "public" view
        # is what the public actually sees. A looser one here would put rows
        # in front of an admin labelled public that no one else can open.
        where.extend(PUBLIC_ONLY)
    if assignee:
        where.append(Feedback.assignee_handle == assignee)
    if q:
        where.append(matching(q))
    if since is not None:
        where.append(Feedback.created_at >= since)
    if resolved_since is not None:
        where.append(repository._reached_since(FeedbackStatus.resolved, resolved_since))
    if deployed_since is not None:
        where.append(repository._reached_since(FeedbackStatus.deployed, deployed_since))
    rows = list(
        (
            await repository._session.execute(
                repository._list_stmt(
                    where=where, sort=sort, limit=limit, offset=offset, q=q
                )
            )
        )
        .scalars()
        .all()
    )
    return rows, await repository._count(where)


async def list_related_to(
    repository: pkg.FeedbackRepository,
    handle: str,
    *,
    is_admin: bool,
    limit: int,
    offset: int,
) -> tuple[list[Feedback], int]:
    """「我的反馈」：我提的 + agent 替我提的 + 指派给我的（且我看得到的）。

    One query with an OR rather than three: the list is one list, and the
    `author_handle` / `submitted_by_handle` / `assignee_handle` indexes each
    serve one arm of it.

    The 指派给我的 arm is the only narrowed one. Being handed a report is work,
    not access — the visibility union is 提交者 ∪ 管理员 ∪ 提出它的房间, and
    being the assignee grants no management power (§8.9). So `visible_to` is
    ANDed onto that arm. Without it this was the one read path that skipped
    the predicate in the module docstring, handing a third party the title of
    a report that `may_see` then refused with a 404 — a list that disagrees
    with its own rows.

    `visible_to` is a superset of `may_see`, not the whole of it, so the rows
    this returns are **not yet** the answer: `FeedbackService.list_mine` makes
    the last cut with `may_see` itself. Both halves are needed and neither is
    optional — see `visible_to` for which half lives where and why.
    """
    where = [
        or_(
            Feedback.author_handle == handle,
            Feedback.submitted_by_handle == handle,
            and_(
                Feedback.assignee_handle == handle,
                visible_to(handle, is_admin=is_admin),
            ),
        )
    ]
    rows = list(
        (
            await repository._session.execute(
                repository._list_stmt(
                    where=where, sort="new", limit=limit, offset=offset
                )
            )
        )
        .scalars()
        .all()
    )
    return rows, await repository._count(where)


async def list_timeline(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID
) -> list[FeedbackTimeline]:
    stmt = (
        select(FeedbackTimeline)
        .where(FeedbackTimeline.feedback_id == feedback_id)
        .order_by(FeedbackTimeline.at.asc(), FeedbackTimeline.id.asc())
    )
    return list((await repository._session.execute(stmt)).scalars().all())


async def list_notes(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID
) -> list[FeedbackNote]:
    stmt = (
        select(FeedbackNote)
        .where(FeedbackNote.feedback_id == feedback_id)
        .order_by(FeedbackNote.created_at.asc(), FeedbackNote.id.asc())
    )
    return list((await repository._session.execute(stmt)).scalars().all())


async def get_read_state(
    repository: pkg.FeedbackRepository, handle: str
) -> FeedbackReadState | None:
    stmt = select(FeedbackReadState).where(FeedbackReadState.user_handle == handle)
    return (await repository._session.execute(stmt)).scalar_one_or_none()


async def latest_activity_of(
    repository: pkg.FeedbackRepository, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Newest comment-or-status time per item, for 「最近活动」 sorting/display."""
    if not ids:
        return {}
    out: dict[uuid.UUID, datetime] = {}
    comment_stmt = (
        select(FeedbackComment.feedback_id, func.max(FeedbackComment.created_at))
        .where(
            FeedbackComment.feedback_id.in_(list(ids)),
            # Same clause as the counts and the thread: a reply nobody can see
            # must not be what this row reports as its latest activity either.
            live_comment_clause(),
        )
        .group_by(FeedbackComment.feedback_id)
    )
    for feedback_id, at in (await repository._session.execute(comment_stmt)).all():
        if at is not None:
            out[feedback_id] = at
    event_stmt = (
        select(FeedbackTimeline.feedback_id, func.max(FeedbackTimeline.at))
        .where(FeedbackTimeline.feedback_id.in_(list(ids)))
        .group_by(FeedbackTimeline.feedback_id)
    )
    for feedback_id, at in (await repository._session.execute(event_stmt)).all():
        if at is not None and (feedback_id not in out or at > out[feedback_id]):
            out[feedback_id] = at
    return out
