"""反馈的计数与序列：面板数字、折线、未指派数。

窗口读的判据 ——「到过」问的是时间线而不是列、同一个状态可以有多行所以数的是
`distinct feedback_id` —— 和别的判据一样住在 `queries.py`，这里只管执行。
`_reached_since` 不碰 session —— 它只是那段判据的入口 —— 所以类上那条转发是
`@staticmethod`，不带实例，这里的函数也就没有 `repository` 参数。

`FeedbackRepository` 上的同名方法是转发。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, exists, func, or_, select

from app.domain.day_windows import utc_day
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackPriority,
    FeedbackStatus,
    FeedbackTimeline,
    FeedbackVisibility,
)
from app.domain.feedback.queries import (
    CLOSED_STATUSES,
    PUBLIC_ONLY,
    live_comment_clause,
    visible_to,
)

if TYPE_CHECKING:
    from app.domain.feedback import repositories as pkg


async def count_author_since(
    repository: pkg.FeedbackRepository, handle: str, since: datetime
) -> int:
    """How many reports this person has filed since a moment.

    Counts what they WROTE, not what they sent: `author_handle` is the same
    column the cap's message names, and on the direct path the author is the
    caller. Deleted rows count too — the cap is about how much somebody is
    producing, and deleting a report is not a way to buy more quota.
    """
    stmt = select(func.count(Feedback.id)).where(
        Feedback.author_handle == handle,
        Feedback.created_at >= since,
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def _count(repository: pkg.FeedbackRepository, where: Sequence[Any]) -> int:
    stmt = select(func.count(Feedback.id)).where(Feedback.deleted_at.is_(None), *where)
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def public_counts(repository: pkg.FeedbackRepository) -> dict[str, int]:
    """The three tab numbers, in one round trip each — returned together.

    Separate requests would render a row of 0s and then jump, and the thing
    that jumps is the first thing on the page.
    """
    where = list(PUBLIC_ONLY)
    all_count = await repository._count([*where, *repository._tab_where("all")])
    resolved_count = await repository._count(
        [*where, *repository._tab_where("resolved")]
    )
    # 「上线」那一个数，单独给。`resolved` 那一栏装的是全部办完的
    # （见 `_tab_where` 的 docstring），那个口径不改：对提交的人来说那都是一个
    # 答复。这只是**另外**多报一个数，让看板上「解决」和「上线」两条线画
    # 得出来 —— 缺了它，「上线了多少」在这个平台上从来没有被数过。
    deployed_count = await repository._count(
        [*where, Feedback.status == FeedbackStatus.deployed]
    )
    hot_base = [*where, *repository._tab_where("hot")]
    # `_tab_where("hot")`, not a second copy of the condition: this is the
    # number on the tab and the rows behind it, and the two were one status
    # apart — a finished suggestion is in the hot list (it does not sink; see
    # the sink rule above) but was not in the number, so the tab counted fewer
    # than it opened. Note the second thing that went wrong there: whoever
    # wrote the count spelled 「办完了」 as a literal `!= resolved`, and when
    # `deployed` was added the copy was silently a status behind.
    # `CLOSED_STATUSES` is why that cannot happen to this one.
    #
    # The docstring above promises one definition for list and counts; this is
    # what that costs when it is not kept.
    # `_hot_ids(hot_base)` — the same subquery `list_public` filters with. The
    # floor makes this number *not* "how many cleared the bar": on a young board
    # it is 「这一栏至少有 5 条」, which is what the tab will open to. Counting
    # only the qualifiers would print 0 on a tab that shows 5 rows.
    hot_count = await repository._count(
        [*hot_base, Feedback.id.in_(repository._hot_ids(hot_base))]
    )
    return {
        "all": all_count,
        "hot": hot_count,
        "resolved": resolved_count,
        "deployed": deployed_count,
    }


def _reached_since(status: FeedbackStatus, since: datetime) -> Any:
    """这条反馈在 `since` 之后**到过**这个状态 —— 一条 `EXISTS`。

    状态变迁只记在 `feedback_timeline` 上，所以这里问的是时间线，`at >= since`
    走 `ix_feedback_timeline_at`（跨反馈的范围条件，既有那条复合索引的打头列是
    `feedback_id`，用不上）。

    **不为此给 `feedback` 加 `resolved_at` / `deployed_at`**：那要回填历史（时间线
    里有、但列上没有），还要在每个写状态的路径上双写，而两处记同一件事迟早会漂开
    —— 时间线本来就把「什么时候到过这里」记着了（见 `FeedbackTimeline` 的
    docstring：「什么时候到过这里，不是现在在哪」）。
    """
    return exists().where(
        FeedbackTimeline.feedback_id == Feedback.id,
        FeedbackTimeline.status == status,
        FeedbackTimeline.at >= since,
    )


async def created_series(
    repository: pkg.FeedbackRepository, *, since: datetime, until: datetime
) -> dict[date, int]:
    """窗口内按 **UTC 的天**新建的反馈数，稀疏；补 0 由调用方做。"""
    day = utc_day(Feedback.created_at)
    stmt = (
        select(day.label("day"), func.count(Feedback.id))
        .where(
            Feedback.deleted_at.is_(None),
            Feedback.created_at >= since,
            Feedback.created_at < until,
        )
        .group_by(day)
        .order_by(day)
    )
    rows = (await repository._session.execute(stmt)).all()
    return {row[0].date(): int(row[1]) for row in rows}


async def reached_series(
    repository: pkg.FeedbackRepository,
    *,
    status: FeedbackStatus,
    since: datetime,
    until: datetime,
) -> dict[date, int]:
    """窗口内按 **UTC 的天**「到过」这个状态的反馈数，稀疏。

    `count(DISTINCT feedback_id)` 而不是 `count(*)`：同一个状态可以有第二行
    （改了又改回来，见 `FeedbackTimeline` 的 docstring），行数是「到过几次」，
    而这条折线画的是「几条反馈」。
    """
    day = utc_day(FeedbackTimeline.at)
    stmt = (
        select(
            day.label("day"),
            func.count(func.distinct(FeedbackTimeline.feedback_id)),
        )
        .where(
            FeedbackTimeline.status == status,
            FeedbackTimeline.at >= since,
            FeedbackTimeline.at < until,
        )
        .group_by(day)
        .order_by(day)
    )
    rows = (await repository._session.execute(stmt)).all()
    return {row[0].date(): int(row[1]) for row in rows}


async def count_created_between(
    repository: pkg.FeedbackRepository, *, since: datetime, until: datetime
) -> int:
    """窗口内新建的反馈总数 —— `created_series` 的合计版（看板环比用）。

    和 `created_series` 同一个 WHERE（未删 + 半开窗口），只是不分桶：环比要的
    是「上一个窗口一共多少」，不是它的形状。两处各写一份判据的话，合计和曲线
    迟早对不上。
    """
    stmt = select(func.count(Feedback.id)).where(
        Feedback.deleted_at.is_(None),
        Feedback.created_at >= since,
        Feedback.created_at < until,
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def count_reached_resolved_between(
    repository: pkg.FeedbackRepository, *, since: datetime, until: datetime
) -> int:
    """窗口内「到过 resolved」的反馈总数 —— `reached_series` 的合计版。

    `count(DISTINCT feedback_id)`，判据和 `reached_series` 同一条（见那里的
    docstring）：解决数是「几条反馈到过这里」，不是「时间线上有几行」。
    """
    stmt = select(func.count(func.distinct(FeedbackTimeline.feedback_id))).where(
        FeedbackTimeline.status == FeedbackStatus.resolved,
        FeedbackTimeline.at >= since,
        FeedbackTimeline.at < until,
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def open_count_by(
    repository: pkg.FeedbackRepository, handles: Sequence[str]
) -> int:
    """Unresolved reports filed by these handles — the agent quota's read."""
    if not handles:
        return 0
    stmt = select(func.count(Feedback.id)).where(
        Feedback.deleted_at.is_(None),
        Feedback.author_handle.in_(list(handles)),
        Feedback.status.not_in(list(CLOSED_STATUSES)),
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def count_activity_since(
    repository: pkg.FeedbackRepository,
    handles: Sequence[str],
    since: datetime | None,
    *,
    is_admin: bool = False,
) -> int:
    """New comments + status events on the items these handles are party to.

    Two event tables, counted separately then summed — a UNION would have to
    dedupe rows that carry no shared id, and the sum of two indexed counts is
    cheaper than the sort that costs.

    The 指派给我的 arm carries `visible_to`, exactly as `list_related_to`
    does and for the same reason: being handed a report is work, not access.
    Without it a non-admin assignee of a private row got an unread number for
    a report that the list filters out and the detail endpoint answers with a
    404 — a count saying "something happened" about a row they can never
    open, and that cannot be cleared because there is nothing to read.
    """
    if not handles:
        return 0
    who = list(handles)
    mine = or_(
        Feedback.author_handle.in_(who),
        Feedback.submitted_by_handle.in_(who),
        *[
            and_(
                Feedback.assignee_handle == handle,
                visible_to(handle, is_admin=is_admin),
            )
            for handle in who
        ],
    )
    comments_stmt = (
        select(func.count(FeedbackComment.id))
        .join(Feedback, Feedback.id == FeedbackComment.feedback_id)
        .where(
            mine,
            Feedback.deleted_at.is_(None),
            # `live_comment_clause()`, not `deleted_at IS NULL`: a reply whose
            # parent was soft-deleted is not visible in the thread, so counting
            # it here is the one thing this number must not do — the bell would
            # say 1 and the thread would show nothing new. It is also what
            # `live_comment_clause`'s own docstring promises every reader here
            # shares, and this was the one that did not.
            live_comment_clause(),
            # My own words are not news to me.
            FeedbackComment.author_handle.notin_(who),
        )
    )
    timeline_stmt = (
        select(func.count(FeedbackTimeline.id))
        .join(Feedback, Feedback.id == FeedbackTimeline.feedback_id)
        .where(
            mine,
            Feedback.deleted_at.is_(None),
            or_(
                FeedbackTimeline.by_handle.is_(None),
                FeedbackTimeline.by_handle.notin_(who),
            ),
        )
    )
    if since is not None:
        comments_stmt = comments_stmt.where(FeedbackComment.created_at > since)
        timeline_stmt = timeline_stmt.where(FeedbackTimeline.at > since)
    comments = int((await repository._session.execute(comments_stmt)).scalar_one() or 0)
    events = int((await repository._session.execute(timeline_stmt)).scalar_one() or 0)
    return comments + events


async def unassigned_count(repository: pkg.FeedbackRepository) -> int:
    """The admin's 「还没人管」 number, asked once per admin list render.

    Deliberately not narrowed by `PUBLIC_ONLY`: "nobody has picked this up"
    spans the private and security queues too, and that is the number the
    分诊台 acts on. It is therefore an **admin-only** read — `FeedbackService.
    counts` is the only caller and it only asks when the caller is an admin,
    because this count, unlike the public tabs, describes rows the caller may
    not be allowed to open.
    """
    stmt = select(func.count(Feedback.id)).where(
        Feedback.deleted_at.is_(None),
        Feedback.assignee_handle.is_(None),
        Feedback.status.not_in(list(CLOSED_STATUSES)),
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def admin_counts(repository: pkg.FeedbackRepository) -> dict[str, dict[str, int]]:
    """The admin dashboard's numbers — the WHOLE board, not the public arm.

    `public_counts` is the user-side tab arithmetic: it is narrowed by
    `PUBLIC_ONLY` because a non-admin must not learn how many private rows
    exist. The admin dashboard asks the opposite question (「现在一共有多少
    事」), and feeding it `public_counts` is the bug this method exists to
    fix — every KPI on that page then described only the public arm while
    the series beside it counted everything, so a card and the line under it
    answered two different questions and both looked right.

    Three buckets, and the split is the admin UI's own vocabulary:

    * `columns` — the queue's four columns, re-spelled as counts. **They are
      filters, not a partition**: `agent` is a source (it overlaps public and
      private), exactly as `list_admin` reads them, so they deliberately do
      not sum to `total`.
    * `status` — every rung of the ladder, full board. This is the only place
      the four rungs are shown side by side; `public_counts` only ever had
      `resolved` (a filter, not a partition — see `_tab_where`).
    * `total` — the one-number answers. `open` / `closed` use the same
      `CLOSED_STATUSES` as everywhere else, so a fifth rung lands here too.

    One round trip per bucket rather than one giant CASE: this table is
    still small (rows are filed by humans and agents, not per API call), and
    three readable queries beat one that nobody can verify against
    `list_admin`.
    """
    live = [Feedback.deleted_at.is_(None)]

    async def _count(*where: Any) -> int:
        return await repository._count([*live, *where])

    columns = {
        "public": await _count(*PUBLIC_ONLY),
        "private": await _count(
            Feedback.visibility == FeedbackVisibility.private,
            Feedback.security.is_(False),
        ),
        "agent": await _count(Feedback.author_is_agent.is_(True)),
        "security": await _count(Feedback.security.is_(True)),
    }
    status = {
        status.value: await _count(Feedback.status == status)
        for status in FeedbackStatus
    }
    total = {
        "all": await _count(),
        "open": await _count(Feedback.status.not_in(list(CLOSED_STATUSES))),
        "closed": await _count(Feedback.status.in_(list(CLOSED_STATUSES))),
        "unassigned": await repository.unassigned_count(),
        # 「压着没人管的急件」— 分诊台最该先动的一格。全量口径，和 `unassigned`
        # 同一条边界（`CLOSED_STATUSES`），只是再收一层优先级。
        "urgent_open": await _count(
            Feedback.status.not_in(list(CLOSED_STATUSES)),
            Feedback.priority.in_([FeedbackPriority.high, FeedbackPriority.urgent]),
        ),
    }
    return {"columns": columns, "status": status, "total": total}
