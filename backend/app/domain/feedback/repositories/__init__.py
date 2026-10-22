"""Feedback data access. No policy in here — visibility lives in `services/`.

Every method takes and returns rows; "who is allowed to see this" is a service
question, so that a route can never get it right by accident and a test can
exercise the rule without a database.

`HOT_SCORE` / `HOT_HALF_LIFE_DAYS` / `HOT_MIN_ITEMS` define the 「热门」 tab; the tab,
its count and the detail card's 「热门」 badge have to agree, and two copies of a
threshold is how they stop agreeing. `hot_score()` is the single expression behind
all three. **它们和条件、游标、排序一起住在 `queries.py`** —— 这一层只管拿一条
session 把那些表达式执行掉。拆开的理由是文件体积（`backend/app/` 单文件 1500 行的
上限），不是分层：界限就是「怎么问」与「去问」这一条。

**This package is still the only entry point to those rows.** 它到 2026-10-15 为止
是一个 1200 行的模块；方法体搬进了同级子模块 —— `reads`（单条、列表、时间线、
备注、已读状态）、`comments`（支持、评论与回复、点赞、评论计数）、`writes`（新建、
状态、指派、关系、时间线、备注）、`stats`（计数与序列）。`FeedbackRepository` 上的
每个方法都是一句转发，转到子模块里同名的函数（实例作为第一个参数），所以导入路径、
公开符号和 patch 点（`services/`、集成测试和按字符串打的 monkeypatch）都不变。

转发方法不做任何决定 —— 那正是两份副本不会走岔的原因，也是这段 docstring 上面
那些话在说的事。拆成**包**而不是几个同级模块，是为了不欠新债：子模块 import 的是
父包这个节点，而「子指向祖先」那条边契约本来就不算环。形状和
`app/domain/feedback/services/` 一样。

`queries.py` 的名字在下面再导出一次：仓库内外一直按
`from app.domain.feedback.repositories import ...` 取它们，换一个文件不该让任何一行
调用点跟着改。`batched` / `_IN_BATCH` 是**故意留在这里的例外** ——
`tests/integration/test_feedback.py` 是按**本模块**的对象把批大小压到 2 来验分块的，
搬去子模块之后那条测试就再也压不动它、等于不再验分块。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.models import (
    Feedback,
    FeedbackAttachment,
    FeedbackComment,
    FeedbackKind,
    FeedbackNote,
    FeedbackPriority,
    FeedbackReadState,
    FeedbackStatus,
    FeedbackTimeline,
    FeedbackVisibility,
)

# 这两个数**住在 `paging.py`**（理由写在那儿：路由也要用它们，而路由不许 import
# repository）。在这里再导出一遍，是因为本模块和 `services/` 一直按 `THREAD_PAGE`
# 的名字用它，名字留在原处比让每个调用点改一行强。不是给路由用的。
from app.domain.feedback.paging import REPLIES_PAGE, THREAD_PAGE

# 这些名字原来**定义在本模块**，拆分后定义在 `queries.py`。这条 import 不是「顺手
# 拿来用」，是把公开路径留在原地：仓库内外 `from app.domain.feedback.repositories
# import ...` 的取法和按字符串打的 monkeypatch 都不用改。所以 F401 在这里是故意的。
from app.domain.feedback.queries import (  # noqa: F401
    CLOSED_STATUSES,
    HOT_HALF_LIFE_DAYS,
    HOT_MIN_ITEMS,
    HOT_SCORE,
    PUBLIC_ONLY,
    CommentPage,
    ListingQueries,
    _group_by_parent,
    _like_pattern,
    after_cursor,
    cursor_of,
    filed_in_a_room_of,
    hot_score,
    live_comment_clause,
    matching,
    parse_cursor,
    search_rank,
    visible_to,
)

from . import attachments, comments, reads, stats, writes

#: 一次 `IN (...)` 里最多放几个值。**这不是微优化**：asyncpg 把参数个数编进 int16，
#: 超过 32767 直接抛 `InterfaceError` —— 那是谁都过不去的 500，而且楼里评论一多，
#: 详情页对**所有人**一起打不开、一直不恢复。分批之后每一批都远在线的这一侧。
_IN_BATCH = 500


def batched(values: Sequence[Any], size: int | None = None) -> Iterator[Sequence[Any]]:
    """把一串值切成每段最多 `size` 个。见 `_IN_BATCH` 里那条不这么做的后果。

    `size` 是 `None` 时读**当时**的 `_IN_BATCH`，而不是把 `_IN_BATCH` 当默认值写死
    在签名里：写成默认值的话，这个模块常量就再也改不动了（默认值在 def 那一刻就
    定下来了），连测试也没法把批大小压小 —— 而「分批之后合并出来的答案对不对」正是
    需要在小批大小下验的那件事（见
    `test_chunked_in_queries_answer_the_same_as_one_big_one`）。
    """
    items = list(values)
    chunk = _IN_BATCH if size is None else size
    for start in range(0, len(items), chunk):
        yield items[start : start + chunk]


class FeedbackRepository(ListingQueries):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 主表 ---------------------------------------------------------------

    async def lock_author(self, handle: str) -> None:
        return await writes.lock_author(self, handle=handle)

    async def count_author_since(self, handle: str, since: datetime) -> int:
        return await stats.count_author_since(self, handle=handle, since=since)

    async def add(
        self,
        *,
        title: str,
        summary: str,
        kind: FeedbackKind,
        visibility: FeedbackVisibility,
        problem: str,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        why: str | None = None,
        expectation: str | None = None,
        what_happened: str | None = None,
        repro: str | None = None,
        evidence: str | None = None,
        logs: str | None = None,
        session_id: str | None = None,
        environment: str | None = None,
        submitted_by_handle: str | None = None,
        submitted_by_user_id: int | None = None,
        topic_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        priority: FeedbackPriority = FeedbackPriority.normal,
        tags: list[str] | None = None,
    ) -> Feedback:
        return await writes.add(
            self,
            title=title,
            summary=summary,
            kind=kind,
            visibility=visibility,
            problem=problem,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            why=why,
            expectation=expectation,
            what_happened=what_happened,
            repro=repro,
            evidence=evidence,
            logs=logs,
            session_id=session_id,
            environment=environment,
            submitted_by_handle=submitted_by_handle,
            submitted_by_user_id=submitted_by_user_id,
            topic_id=topic_id,
            project_id=project_id,
            priority=priority,
            tags=tags,
        )

    async def get(self, feedback_id: uuid.UUID) -> Feedback | None:
        return await reads.get(self, feedback_id=feedback_id)

    async def filed_in_a_room_of_mine(
        self, feedback_id: uuid.UUID, handle: str
    ) -> bool:
        return await reads.filed_in_a_room_of_mine(
            self, feedback_id=feedback_id, handle=handle
        )

    async def _count(self, where: Sequence[Any]) -> int:
        return await stats._count(self, where=where)

    async def list_public(
        self,
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
        return await reads.list_public(
            self,
            tab=tab,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
            author=author,
            status=status,
            kind=kind,
            since=since,
        )

    async def public_counts(self) -> dict[str, int]:
        return await stats.public_counts(self)

    async def list_admin(
        self,
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
        return await reads.list_admin(
            self,
            tab=tab,
            assignee=assignee,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
            since=since,
            resolved_since=resolved_since,
            deployed_since=deployed_since,
        )

    # --- 平台看板的两个时间读 --------------------------------------------------

    @staticmethod
    def _reached_since(status: FeedbackStatus, since: datetime) -> Any:
        return stats._reached_since(status=status, since=since)

    async def created_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        return await stats.created_series(self, since=since, until=until)

    async def reached_series(
        self, *, status: FeedbackStatus, since: datetime, until: datetime
    ) -> dict[date, int]:
        return await stats.reached_series(self, status=status, since=since, until=until)

    async def count_created_between(self, *, since: datetime, until: datetime) -> int:
        return await stats.count_created_between(self, since=since, until=until)

    async def count_reached_resolved_between(
        self, *, since: datetime, until: datetime
    ) -> int:
        return await stats.count_reached_resolved_between(
            self, since=since, until=until
        )

    async def list_related_to(
        self, handle: str, *, is_admin: bool, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        return await reads.list_related_to(
            self, handle=handle, is_admin=is_admin, limit=limit, offset=offset
        )

    async def set_status(self, row: Feedback, status: FeedbackStatus) -> None:
        return await writes.set_status(self, row=row, status=status)

    async def set_priority(self, row: Feedback, priority: FeedbackPriority) -> None:
        return await writes.set_priority(self, row=row, priority=priority)

    async def set_assignee(self, row: Feedback, assignee: str | None) -> None:
        return await writes.set_assignee(self, row=row, assignee=assignee)

    async def set_security(self, row: Feedback, security: bool) -> None:
        return await writes.set_security(self, row=row, security=security)

    async def open_count_by(self, handles: Sequence[str]) -> int:
        return await stats.open_count_by(self, handles=handles)

    # --- 支持 -----------------------------------------------------------------

    async def supports_count(self, feedback_id: uuid.UUID) -> int:
        return await comments.supports_count(self, feedback_id=feedback_id)

    async def supports_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await comments.supports_counts(self, ids=ids)

    async def supported_by(
        self, ids: Sequence[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        return await comments.supported_by(self, ids=ids, handle=handle)

    async def has_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        return await comments.has_support(self, feedback_id=feedback_id, handle=handle)

    async def add_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        return await writes.add_support(self, feedback_id=feedback_id, handle=handle)

    async def remove_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        return await writes.remove_support(self, feedback_id=feedback_id, handle=handle)

    # --- 评论 -----------------------------------------------------------------

    async def page_comments(
        self,
        feedback_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = THREAD_PAGE,
        replies_limit: int = REPLIES_PAGE,
    ) -> CommentPage:
        return await comments.page_comments(
            self,
            feedback_id=feedback_id,
            after=after,
            limit=limit,
            replies_limit=replies_limit,
        )

    async def page_replies(
        self,
        parent_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = REPLIES_PAGE,
    ) -> tuple[list[FeedbackComment], str | None]:
        return await comments.page_replies(
            self, parent_id=parent_id, after=after, limit=limit
        )

    async def _replies_of(
        self, top_ids: Sequence[uuid.UUID], *, limit: int
    ) -> list[FeedbackComment]:
        return await comments._replies_of(self, top_ids=top_ids, limit=limit)

    async def _reply_counts(self, top_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await comments._reply_counts(self, top_ids=top_ids)

    async def get_comment(self, comment_id: uuid.UUID) -> FeedbackComment | None:
        return await comments.get_comment(self, comment_id=comment_id)

    async def add_comment(
        self,
        *,
        feedback_id: uuid.UUID,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        body: str,
        parent_id: uuid.UUID | None,
        reply_to_handle: str | None,
    ) -> FeedbackComment:
        return await writes.add_comment(
            self,
            feedback_id=feedback_id,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            body=body,
            parent_id=parent_id,
            reply_to_handle=reply_to_handle,
        )

    async def soft_delete_comment(self, row: FeedbackComment) -> None:
        return await writes.soft_delete_comment(self, row=row)

    async def soft_delete_feedback(self, row: Feedback) -> None:
        return await writes.soft_delete_feedback(self, row=row)

    # --- 评论点赞 -------------------------------------------------------------
    #
    # Read side is batched for the whole thread, for the same reason the report
    # counts are (`supports_counts`): a thread is a page of rows and the
    # alternative is two queries per reply.

    async def comment_like_counts(
        self, comment_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        return await comments.comment_like_counts(self, comment_ids=comment_ids)

    async def comment_like_count(self, comment_id: uuid.UUID) -> int:
        return await comments.comment_like_count(self, comment_id=comment_id)

    async def comment_liked_by(
        self, comment_ids: Sequence[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        return await comments.comment_liked_by(
            self, comment_ids=comment_ids, handle=handle
        )

    async def add_comment_like(self, comment_id: uuid.UUID, handle: str) -> bool:
        return await writes.add_comment_like(self, comment_id=comment_id, handle=handle)

    async def remove_comment_like(self, comment_id: uuid.UUID, handle: str) -> bool:
        return await writes.remove_comment_like(
            self, comment_id=comment_id, handle=handle
        )

    # --- 时间线 ---------------------------------------------------------------

    async def list_timeline(self, feedback_id: uuid.UUID) -> list[FeedbackTimeline]:
        return await reads.list_timeline(self, feedback_id=feedback_id)

    async def append_timeline(
        self, feedback_id: uuid.UUID, status: FeedbackStatus, by_handle: str | None
    ) -> FeedbackTimeline:
        return await writes.append_timeline(
            self, feedback_id=feedback_id, status=status, by_handle=by_handle
        )

    # --- 备注 -----------------------------------------------------------------

    async def list_notes(self, feedback_id: uuid.UUID) -> list[FeedbackNote]:
        return await reads.list_notes(self, feedback_id=feedback_id)

    async def add_note(
        self, feedback_id: uuid.UUID, author_handle: str, body: str
    ) -> FeedbackNote:
        return await writes.add_note(
            self, feedback_id=feedback_id, author_handle=author_handle, body=body
        )

    # --- 附件 -----------------------------------------------------------------

    async def add_attachment(
        self,
        *,
        attachment_id: uuid.UUID,
        uploader_handle: str,
        name: str,
        content_type: str,
        size: int,
        location: str,
        blob_key: str,
    ) -> FeedbackAttachment:
        return await attachments.add(
            self,
            attachment_id=attachment_id,
            uploader_handle=uploader_handle,
            name=name,
            content_type=content_type,
            size=size,
            location=location,
            blob_key=blob_key,
        )

    async def get_attachment(
        self, attachment_id: uuid.UUID
    ) -> FeedbackAttachment | None:
        return await attachments.get(self, attachment_id=attachment_id)

    async def get_attachments(self, ids: list[uuid.UUID]) -> list[FeedbackAttachment]:
        return await attachments.get_many(self, ids=ids)

    async def list_attachments(
        self, feedback_id: uuid.UUID
    ) -> list[FeedbackAttachment]:
        return await attachments.list_for_feedback(self, feedback_id=feedback_id)

    async def attach_attachments(
        self, *, rows: list[FeedbackAttachment], feedback_id: uuid.UUID
    ) -> None:
        return await attachments.attach(self, rows=rows, feedback_id=feedback_id)

    # --- 未读 cursor ----------------------------------------------------------

    async def get_read_state(self, handle: str) -> FeedbackReadState | None:
        return await reads.get_read_state(self, handle=handle)

    async def bump_read_state(self, handle: str, at: datetime) -> FeedbackReadState:
        return await writes.bump_read_state(self, handle=handle, at=at)

    async def count_activity_since(
        self,
        handles: Sequence[str],
        since: datetime | None,
        *,
        is_admin: bool = False,
    ) -> int:
        return await stats.count_activity_since(
            self, handles=handles, since=since, is_admin=is_admin
        )

    async def latest_activity_of(
        self, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return await reads.latest_activity_of(self, ids=ids)

    async def unassigned_count(self) -> int:
        return await stats.unassigned_count(self)

    async def admin_counts(self) -> dict[str, dict[str, int]]:
        return await stats.admin_counts(self)

    async def comment_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await comments.comment_counts(self, ids=ids)
