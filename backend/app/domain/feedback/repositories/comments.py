"""反馈的评论面：支持、评论与回复、点赞、评论计数。

评论是两层深的：折上去的规则（回复的回复挂在父评论下面，不再往下一层）在
`queries.py` 的 `_group_by_parent` 里，这里负责把它和分页、计数一起执行掉。
这一面只有**读和计数**（有没有支持、谁支持过、多少条评论、谁点过赞）；写的那几笔
（`add_support` / `add_comment` / `add_comment_like` 这一组）在 `writes` 里。

分批那一处是 `comments.py` 回指父包的原因：`batched` 和它读的 `_IN_BATCH`
按 `tests/integration/test_feedback.py` 的 monkeypatch 点留在
`repositories/__init__.py`，所以这里取的是包的属性而不是本模块的名字 ——
`batched(comment_ids)` 得写成 `repo.batched(comment_ids)`。

`FeedbackRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.domain.feedback import repositories as repo
from app.domain.feedback.models import (
    FeedbackComment,
    FeedbackCommentLike,
    FeedbackSupport,
)
from app.domain.feedback.paging import REPLIES_PAGE, THREAD_PAGE
from app.domain.feedback.queries import (
    CommentPage,
    _group_by_parent,
    after_cursor,
    cursor_of,
    live_comment_clause,
)

if TYPE_CHECKING:
    from app.domain.feedback import repositories as pkg


async def supports_count(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID
) -> int:
    stmt = select(func.count(FeedbackSupport.id)).where(
        FeedbackSupport.feedback_id == feedback_id
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def supports_counts(
    repository: pkg.FeedbackRepository, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Support counts for a page, in one query.

    The alternative — a count per row — is N+1 on the very list that sorts
    by this number.
    """
    if not ids:
        return {}
    stmt = (
        select(FeedbackSupport.feedback_id, func.count(FeedbackSupport.id))
        .where(FeedbackSupport.feedback_id.in_(list(ids)))
        .group_by(FeedbackSupport.feedback_id)
    )
    return {
        row[0]: int(row[1]) for row in (await repository._session.execute(stmt)).all()
    }


async def supported_by(
    repository: pkg.FeedbackRepository, ids: Sequence[uuid.UUID], handle: str
) -> set[uuid.UUID]:
    """Which of these the viewer already supported — the heart's filled state."""
    if not ids:
        return set()
    stmt = select(FeedbackSupport.feedback_id).where(
        FeedbackSupport.feedback_id.in_(list(ids)),
        FeedbackSupport.author_handle == handle,
    )
    return set((await repository._session.execute(stmt)).scalars().all())


async def has_support(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID, handle: str
) -> bool:
    stmt = select(FeedbackSupport.id).where(
        FeedbackSupport.feedback_id == feedback_id,
        FeedbackSupport.author_handle == handle,
    )
    return (await repository._session.execute(stmt)).scalar_one_or_none() is not None


async def page_comments(
    repository: pkg.FeedbackRepository,
    feedback_id: uuid.UUID,
    *,
    after: str | None = None,
    limit: int = THREAD_PAGE,
    replies_limit: int = REPLIES_PAGE,
) -> CommentPage:
    """一页评论。**这是取评论的唯一入口**，没有「一次把整条都取回来」那个版本。

    以前有（`list_comments`，无上限）。它的代价不是慢，是三个一起到：
    一个几千条回复的帖子会把整条线程materialize成 ORM 对象和 JSON；同一条线程的
    评论 id 会进 `comment_like_counts` 的 `IN (...)`，过了 32767 个参数就是一条
    500，而这是详情页的主路径 —— 也就是说那条帖子之后**对所有人**打不开，删
    评论才会好。分页把这三件事一起关掉：一页的 id 数由 `limit × (1 + replies_limit)`
    封顶。

    「楼」是分页单位而不是「条」：客户端按 `parent_id` 分组来画，一栋楼拆到两页
    里就会出现一条回复挂不住的父亲。所以顶层按游标取 `limit` 栋，回复跟着每栋楼
    走，楼内那层由 `replies_limit` 单独封顶。
    """
    tops_stmt = (
        select(FeedbackComment)
        .where(
            FeedbackComment.feedback_id == feedback_id,
            FeedbackComment.parent_id.is_(None),
            # 顶层没有父亲，`live_comment_clause()` 在这里就是这一句。
            FeedbackComment.deleted_at.is_(None),
        )
        .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
        # 多取一条只用来回答「还有没有下一页」，它不进返回值。
        .limit(limit + 1)
    )
    if after:
        tops_stmt = after_cursor(tops_stmt, after)
    tops = list((await repository._session.execute(tops_stmt)).scalars().all())
    more = len(tops) > limit
    tops = tops[:limit]
    if not tops:
        return CommentPage(rows=[], next_cursor=None, reply_counts={}, reply_cursors={})

    top_ids = [top.id for top in tops]
    # 每栋楼多取一条：多的那一条不进返回值，只用来回答「这一栋楼里还有下一页
    # 吗」。取 `limit` 条再猜「大概取完了吧」是不行的 —— 一栋正好 50 条的楼会被
    # 判成还有下一页，客户端于是多发一次必然取到空页的请求。
    fetched = await repository._replies_of(top_ids, limit=replies_limit + 1)
    replies: list[FeedbackComment] = []
    cursors: dict[uuid.UUID, str] = {}
    for parent_id, rows in _group_by_parent(fetched).items():
        if len(rows) > replies_limit:
            cursors[parent_id] = cursor_of(rows[replies_limit - 1])
            rows = rows[:replies_limit]
        replies.extend(rows)
    return CommentPage(
        rows=[*tops, *replies],
        next_cursor=cursor_of(tops[-1]) if more else None,
        reply_counts=await repository._reply_counts(top_ids),
        reply_cursors=cursors,
    )


async def page_replies(
    repository: pkg.FeedbackRepository,
    parent_id: uuid.UUID,
    *,
    after: str | None = None,
    limit: int = REPLIES_PAGE,
) -> tuple[list[FeedbackComment], str | None]:
    """一栋楼里的下一段回复。返回 `(rows, next_cursor)`。

    父亲被判掉的那一瞬间，它下面的回复也一起从读侧消失（`live_comment_clause`），
    所以这里直接按 `parent_id` 取就够：调用方刚把这条顶层评论拿在手里，它是不是
    活的已经由那一步回答了。
    """
    stmt = (
        select(FeedbackComment)
        .where(
            FeedbackComment.parent_id == parent_id,
            FeedbackComment.deleted_at.is_(None),
        )
        .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
        .limit(limit + 1)
    )
    if after:
        stmt = after_cursor(stmt, after)
    rows = list((await repository._session.execute(stmt)).scalars().all())
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (cursor_of(rows[-1]) if more and rows else None)


async def _replies_of(
    repository: pkg.FeedbackRepository, top_ids: Sequence[uuid.UUID], *, limit: int
) -> list[FeedbackComment]:
    """这些楼各自的前 `limit` 条回复 —— 一次查询，每栋楼各数各的。

    `row_number() OVER (PARTITION BY parent_id ...)` 是这件事的正解：按楼分窗、
    窗内按 `(created_at, id)` 排序、每窗取前 N。写成「每栋楼一个 LIMIT」是一页
    50 栋楼 50 次查询；写成「先全取回来再在 Python 里截」等于这条上限没生效，
    而这正是要防的那件事。
    """
    ranked = (
        select(
            FeedbackComment.id.label("id"),
            func.row_number()
            .over(
                partition_by=FeedbackComment.parent_id,
                order_by=(
                    FeedbackComment.created_at.asc(),
                    FeedbackComment.id.asc(),
                ),
            )
            .label("rank"),
        )
        .where(
            FeedbackComment.parent_id.in_(list(top_ids)),
            FeedbackComment.deleted_at.is_(None),
        )
        .subquery()
    )
    stmt = (
        select(FeedbackComment)
        .where(
            FeedbackComment.id.in_(select(ranked.c.id).where(ranked.c.rank <= limit))
        )
        .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
    )
    return list((await repository._session.execute(stmt)).scalars().all())


async def _reply_counts(
    repository: pkg.FeedbackRepository, top_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """每栋楼的回复**总数**（不是页里带了几条），一次 `GROUP BY`。"""
    stmt = (
        select(FeedbackComment.parent_id, func.count(FeedbackComment.id))
        .where(
            FeedbackComment.parent_id.in_(list(top_ids)),
            FeedbackComment.deleted_at.is_(None),
        )
        .group_by(FeedbackComment.parent_id)
    )
    return {
        row[0]: int(row[1]) for row in (await repository._session.execute(stmt)).all()
    }


async def get_comment(
    repository: pkg.FeedbackRepository, comment_id: uuid.UUID
) -> FeedbackComment | None:
    stmt = select(FeedbackComment).where(
        FeedbackComment.id == comment_id, FeedbackComment.deleted_at.is_(None)
    )
    return (await repository._session.execute(stmt)).scalar_one_or_none()


async def comment_like_counts(
    repository: pkg.FeedbackRepository, comment_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    counts: dict[uuid.UUID, int] = {}
    for batch in repo.batched(comment_ids):
        stmt = (
            select(
                FeedbackCommentLike.comment_id,
                func.count(FeedbackCommentLike.id),
            )
            .where(FeedbackCommentLike.comment_id.in_(list(batch)))
            .group_by(FeedbackCommentLike.comment_id)
        )
        counts.update(
            {
                row[0]: int(row[1])
                for row in (await repository._session.execute(stmt)).all()
            }
        )
    return counts


async def comment_like_count(
    repository: pkg.FeedbackRepository, comment_id: uuid.UUID
) -> int:
    stmt = select(func.count(FeedbackCommentLike.id)).where(
        FeedbackCommentLike.comment_id == comment_id
    )
    return int((await repository._session.execute(stmt)).scalar_one() or 0)


async def comment_liked_by(
    repository: pkg.FeedbackRepository, comment_ids: Sequence[uuid.UUID], handle: str
) -> set[uuid.UUID]:
    """Which of these the viewer already liked — the button's filled state."""
    liked: set[uuid.UUID] = set()
    for batch in repo.batched(comment_ids):
        stmt = select(FeedbackCommentLike.comment_id).where(
            FeedbackCommentLike.comment_id.in_(list(batch)),
            FeedbackCommentLike.author_handle == handle,
        )
        liked.update((await repository._session.execute(stmt)).scalars().all())
    return liked


async def comment_counts(
    repository: pkg.FeedbackRepository, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Live comment counts for a page — one query, same reason as supports."""
    if not ids:
        return {}
    stmt = (
        select(FeedbackComment.feedback_id, func.count(FeedbackComment.id))
        .where(
            FeedbackComment.feedback_id.in_(list(ids)),
            live_comment_clause(),
        )
        .group_by(FeedbackComment.feedback_id)
    )
    return {
        row[0]: int(row[1]) for row in (await repository._session.execute(stmt)).all()
    }
