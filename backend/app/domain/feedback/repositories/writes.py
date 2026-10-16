"""反馈的写：新建、状态、指派、支持关系、评论、时间线、备注、已读。

写一条不回「谁看得见、谁可以改」—— 那是 `services/` 的判据，进来之前已经问过。
这里是这一层**所有会改行**的方法：读与计数在 `reads` / `comments` / `stats`。
`status` 与 timeline 一起动这一个决定留在 `services/`：这一层只提供写 `status` 和
追加 timeline 两个动作，谁在什么时候两个都做是上层的事。

`FeedbackRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackCommentLike,
    FeedbackKind,
    FeedbackNote,
    FeedbackPriority,
    FeedbackReadState,
    FeedbackStatus,
    FeedbackSupport,
    FeedbackTimeline,
    FeedbackVisibility,
)

if TYPE_CHECKING:
    from app.domain.feedback import repositories as pkg


async def lock_author(repository: pkg.FeedbackRepository, handle: str) -> None:
    """Serialize one author's publishes, for the length of this transaction.

    The daily cap below is a read-then-insert, so without this two requests
    that arrive together both read a count below the cap and both insert —
    the cap fails exactly when it is doing its job, which is the one moment
    it has to hold. A lock is the same answer `proposals.py` gives its own
    cap, and for the same reason: it covers the window that needs covering
    and nothing longer, and dying mid-transaction releases it.

    Namespaced with a string so two features cannot collide on the hash of
    one handle.
    """
    await repository._session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"feedback-reports:{handle}"},
    )


async def add(
    repository: pkg.FeedbackRepository,
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
    row = Feedback(
        title=title,
        summary=summary,
        kind=kind,
        status=FeedbackStatus.received,
        visibility=visibility,
        priority=priority,
        problem=problem,
        why=why,
        expectation=expectation,
        what_happened=what_happened,
        repro=repro,
        evidence=evidence,
        logs=logs,
        session_id=session_id,
        environment=environment,
        author_handle=author_handle,
        author_user_id=author_user_id,
        author_is_agent=author_is_agent,
        submitted_by_handle=submitted_by_handle,
        submitted_by_user_id=submitted_by_user_id,
        topic_id=topic_id,
        project_id=project_id,
        tags=tags or [],
    )
    repository._session.add(row)
    # flush, not commit: the caller's transaction spans the timeline append
    # that must land with it (`services.create`).
    await repository._session.flush()
    return row


async def set_status(
    repository: pkg.FeedbackRepository, row: Feedback, status: FeedbackStatus
) -> None:
    row.status = status
    await repository._session.flush()


async def set_priority(
    repository: pkg.FeedbackRepository, row: Feedback, priority: FeedbackPriority
) -> None:
    row.priority = priority
    await repository._session.flush()


async def set_assignee(
    repository: pkg.FeedbackRepository, row: Feedback, assignee: str | None
) -> None:
    row.assignee_handle = assignee
    await repository._session.flush()


async def set_security(
    repository: pkg.FeedbackRepository, row: Feedback, security: bool
) -> None:
    row.security = security
    await repository._session.flush()


async def add_support(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID, handle: str
) -> bool:
    """Idempotent: a repeat POST is a no-op, not a second row.

    **`ON CONFLICT DO NOTHING` rather than read-then-insert**, and that is a
    correctness fix rather than a micro-optimisation. The pair of statements
    it replaces — `has_support`, then `add` — is a race the unique constraint
    turns into a 500: two taps of the same button overlapping in flight (a
    double-click on a slow connection is enough — the button is not disabled
    while the request is out) both read "not supported", both insert, and the
    loser's `flush` raises `IntegrityError` out of the route. One statement
    cannot lose that race, and it also drops a round trip.

    `RETURNING id` is what keeps the boolean honest: the row comes back only
    when the insert really happened, so "was it written" costs nothing extra.
    """
    stmt = (
        pg_insert(FeedbackSupport)
        .values(feedback_id=feedback_id, author_handle=handle)
        .on_conflict_do_nothing(index_elements=["feedback_id", "author_handle"])
        .returning(FeedbackSupport.id)
    )
    return (await repository._session.execute(stmt)).scalar_one_or_none() is not None


async def remove_support(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID, handle: str
) -> bool:
    """Also idempotent — deleting a support that is not there answers 200."""
    stmt = select(FeedbackSupport).where(
        FeedbackSupport.feedback_id == feedback_id,
        FeedbackSupport.author_handle == handle,
    )
    row = (await repository._session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await repository._session.delete(row)
    await repository._session.flush()
    return True


async def add_comment(
    repository: pkg.FeedbackRepository,
    *,
    feedback_id: uuid.UUID,
    author_handle: str,
    author_user_id: int | None,
    author_is_agent: bool,
    body: str,
    parent_id: uuid.UUID | None,
    reply_to_handle: str | None,
) -> FeedbackComment:
    row = FeedbackComment(
        feedback_id=feedback_id,
        parent_id=parent_id,
        author_handle=author_handle,
        author_user_id=author_user_id,
        author_is_agent=author_is_agent,
        body=body,
        reply_to_handle=reply_to_handle,
    )
    repository._session.add(row)
    await repository._session.flush()
    return row


async def soft_delete_comment(
    repository: pkg.FeedbackRepository, row: FeedbackComment
) -> None:
    """Soft-delete `row`, and the replies that hang under it.

    **The children have to go too, and that is not tidiness.** The client
    renders a thread by taking the comments with no `parent_id` and then
    asking each of those for its replies, while `list_comments` filters
    deleted rows out — so a deleted parent does not merely hide itself:
    every reply under it leaves the screen with it, with no tombstone and
    nothing left that would ever query them again. `FeedbackComment.parent_id`
    already states the intended behaviour («deleting a top-level comment
    takes its replies with it»); this is where that is kept. B站 and 小红书
    answer the same way, and no other reading survives contact with a
    reader: a reply that says 「回复 X」 while X is nowhere on the page is
    worse than either outcome.

    One `UPDATE` for the children rather than a load-and-touch per reply —
    a top-level comment can carry a page of them, and someone is waiting on
    this request. A reply being deleted matches no rows here, which is
    correct rather than lucky: `parent_id` only ever points at a TOP-LEVEL
    comment (the service folds replies onto their grandparent), so a reply
    has no children to begin with.
    """
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await repository._session.execute(
        update(FeedbackComment)
        .where(
            FeedbackComment.parent_id == row.id,
            FeedbackComment.deleted_at.is_(None),
        )
        .values(deleted_at=deleted_at)
    )
    await repository._session.flush()


async def soft_delete_feedback(
    repository: pkg.FeedbackRepository, row: Feedback
) -> None:
    """软删一条反馈，**连同它下面所有还在的评论**。

    和 `soft_delete_comment` 同一个形状、同一个理由：读侧过滤 `deleted_at`
    （`_public_where` / `_admin_where` 与 `live_comment_clause`），所以打了时间戳
    的行从列表、详情、计数和搜索里一起消失。

    **评论必须跟着走**：留下的话，它们挂在一条谁也读不到的反馈下面 —— 楼还在、
    帖子没了，而「这栋楼在回哪条反馈」是永远查不出来的那一半。一条 `UPDATE` 全
    带走，不做逐条，理由同评论那一处（有人正在等这个请求）。

    **不硬删**。这一动作有两个调用者（作者删自己的、管理员删别人的），而管理员
    删的是别人写的东西 —— 那是需要留痕的一类动作，`deleted_at` 就是那条痕。
    """
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await repository._session.execute(
        update(FeedbackComment)
        .where(
            FeedbackComment.feedback_id == row.id,
            FeedbackComment.deleted_at.is_(None),
        )
        .values(deleted_at=deleted_at)
    )
    await repository._session.flush()


async def add_comment_like(
    repository: pkg.FeedbackRepository, comment_id: uuid.UUID, handle: str
) -> bool:
    """`add_support`, one table down — including why it is one statement."""
    stmt = (
        pg_insert(FeedbackCommentLike)
        .values(comment_id=comment_id, author_handle=handle)
        .on_conflict_do_nothing(index_elements=["comment_id", "author_handle"])
        .returning(FeedbackCommentLike.id)
    )
    return (await repository._session.execute(stmt)).scalar_one_or_none() is not None


async def remove_comment_like(
    repository: pkg.FeedbackRepository, comment_id: uuid.UUID, handle: str
) -> bool:
    """Also idempotent — unliking something not liked answers 200."""
    stmt = select(FeedbackCommentLike).where(
        FeedbackCommentLike.comment_id == comment_id,
        FeedbackCommentLike.author_handle == handle,
    )
    row = (await repository._session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await repository._session.delete(row)
    await repository._session.flush()
    return True


async def append_timeline(
    repository: pkg.FeedbackRepository,
    feedback_id: uuid.UUID,
    status: FeedbackStatus,
    by_handle: str | None,
) -> FeedbackTimeline:
    row = FeedbackTimeline(feedback_id=feedback_id, status=status, by_handle=by_handle)
    repository._session.add(row)
    await repository._session.flush()
    return row


async def add_note(
    repository: pkg.FeedbackRepository,
    feedback_id: uuid.UUID,
    author_handle: str,
    body: str,
) -> FeedbackNote:
    row = FeedbackNote(feedback_id=feedback_id, author_handle=author_handle, body=body)
    repository._session.add(row)
    await repository._session.flush()
    return row


async def bump_read_state(
    repository: pkg.FeedbackRepository, handle: str, at: datetime
) -> FeedbackReadState:
    """推游标 —— 一跳 upsert。

    先读后插在这个形状上会 500，和 `add_support` / `add_comment_like` /
    `add_admin` 是同一个：`FeedbackReadState` 上有 `uq_feedback_read_state_user`，
    而同一个人的两条请求真的会同时到（手动 `markRead()` 没有 in-flight 守卫，
    它和 `markReadOnce()` 的去重表是每个标签页各自一份的）。两边都读到「还没有
    这一行」、都插，败者在 flush 抛 `IntegrityError`，没有 handler 认它，于是
    「把游标推到此刻」这个**幂等**动作回了 500。首读之前没有别的地方建这一行
    （没有登录时预置、没有 trigger），所以每个用户第一次推游标都在这条路上。

    要 `do_update` 而不是 `do_nothing`：这一跳既建冷游标也推进已有游标。撞车时
    两条请求要的是同一件事（游标 → 此刻），所以后来者覆盖先到者是对的。
    """
    stmt = (
        pg_insert(FeedbackReadState)
        .values(user_handle=handle, last_read_at=at)
        .on_conflict_do_update(
            index_elements=["user_handle"],
            set_={"last_read_at": at},
        )
        .returning(FeedbackReadState)
    )
    return (await repository._session.execute(stmt)).scalar_one()
