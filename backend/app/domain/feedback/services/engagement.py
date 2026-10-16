"""反馈的支持、评论与删除 —— 从 `services.py` 的「支持 / 评论」那一段拆出来。

评论是两层深的：折上去的规则（回复的回复挂在父评论下面，不再往下一层）抄自原型。
`delete_feedback` 与 `note` 也挂在这里，它们和评论共用同一组「看得见吗 / 能不能删」
的判据。

`FeedbackService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.core.sentences import say
from app.domain.feedback import repositories as repo
from app.domain.feedback.models import Feedback, FeedbackComment
from app.domain.identity.services import IdentityService

if TYPE_CHECKING:
    from app.domain.feedback import services as pkg


async def support(
    svc: pkg.FeedbackService, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
) -> tuple[int, bool]:
    row = await svc.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    if row.status in repo.CLOSED_STATUSES:
        # Supporting something already closed changes nothing anyone will
        # look at, and it inflates the `hot` tab with items nobody can act
        # on. 412 rather than 403: the client's fix is to re-read the item,
        # not to stop asking. `deployed` counts as closed for the same
        # reason `resolved` does — 上线 is further along, not less finished.
        #
        # The message names the action, not a status: the reader may be
        # looking at either closed rung, and 「已解决的不再接受支持」 would be
        # wrong on a 已上线 row (it never said 已解决).
        raise PreconditionFailedError(say("feedbackClosedNoSupport"))
    await svc._repo.add_support(row.id, handle)
    return await svc._repo.supports_count(row.id), True


async def unsupport(
    svc: pkg.FeedbackService, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
) -> tuple[int, bool]:
    row = await svc.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    await svc._repo.remove_support(row.id, handle)
    return await svc._repo.supports_count(row.id), False


async def comment(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    body: str,
    parent_id: uuid.UUID | None,
    *,
    actor_handle: str,
    actor_user_id: int | None,
    is_admin: bool,
) -> tuple[FeedbackComment, Feedback]:
    """Add a comment. Whether its author is an agent is read from the
    handle's agent-binding, not taken from the caller: it is stored on the
    row (the 「芝士回的」 badge), and a stored fact a route hands in is one
    the route can hand in wrong."""
    actor_is_agent = await IdentityService(svc._session).is_agent(actor_handle)
    row = await svc.visible_row(feedback_id, handle=actor_handle, is_admin=is_admin)
    parent = None
    reply_to_handle = None
    if parent_id is not None:
        parent = await svc._repo.get_comment(parent_id)
        if parent is None or parent.feedback_id != row.id:
            # A parent that belongs to another report, or to none: 400, not
            # 404 — the id the client sent is the problem, not a secret.
            raise BadRequestError(say("feedbackReplyParentMismatch"))
        # The target, captured here because the very next line overwrites
        # the thing that points at it. Only when the comment being answered
        # is itself a reply: a reply to the 楼主 already renders directly
        # under it, so 「回复 楼主」 on every 楼内回复 would be noise, and
        # the reference platforms this was asked to match (B站/小红书) draw
        # the prefix exactly where the fold loses the target — which is
        # replies to replies, and only those.
        reply_to_handle = parent.author_handle if parent.parent_id else None
        # Two levels only, folded the same way the prototype does it
        # (`stores/feedback.ts::addComment`): a reply to a reply lands under
        # the same top-level comment. Storing depth-3 would mean the client
        # renders a thread it was not designed for, and the fold is cheaper
        # than teaching every reader to recurse.
        parent_id = parent.parent_id or parent.id
    created = await svc._repo.add_comment(
        feedback_id=row.id,
        author_handle=actor_handle,
        author_user_id=actor_user_id,
        author_is_agent=actor_is_agent,
        body=body,
        parent_id=parent_id,
        reply_to_handle=reply_to_handle,
    )
    return created, row


async def like_comment(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    *,
    handle: str,
    is_admin: bool,
) -> tuple[int, bool]:
    """点赞一条回复。`support`, one level down — the count after the write.

    **Deliberately no `CLOSED_STATUSES` guard**, unlike `support`. A support
    is an input to the 「热门」 tab, so letting people pile onto something
    already 办完了 inflates a ranking nobody can act on — that is why the
    report-level one answers 412. A like ranks nothing: it says 「这条回复说
    得对」, and that stays worth saying under a thread on a closed report,
    which is exactly where the conclusions («原来是这样，我也遇到了») end up.
    Putting the guard here would mean the last useful replies on finished
    items are the ones that cannot be marked.
    """
    await svc._visible_comment(
        feedback_id, comment_id, handle=handle, is_admin=is_admin
    )
    await svc._repo.add_comment_like(comment_id, handle)
    return await svc._repo.comment_like_count(comment_id), True


async def unlike_comment(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    *,
    handle: str,
    is_admin: bool,
) -> tuple[int, bool]:
    await svc._visible_comment(
        feedback_id, comment_id, handle=handle, is_admin=is_admin
    )
    await svc._repo.remove_comment_like(comment_id, handle)
    return await svc._repo.comment_like_count(comment_id), False


async def _visible_comment(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    *,
    handle: str,
    is_admin: bool,
) -> FeedbackComment:
    """The comment, once the caller is allowed to know the report exists.

    Two gates, in this order: the report first (404 for one the caller may
    not see — `visible_row`'s rule, and it must not be skipped or a like on
    an id from a private report answers differently from a read of it), then
    the comment's membership in it. The second is 404 too, not 400: by then
    the caller has already proved they may see the report, so a comment id
    that is not in it is just an id that is not there.
    """
    row = await svc.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    comment = await svc._repo.get_comment(comment_id)
    if comment is None or comment.feedback_id != row.id:
        raise NotFoundError(say("commentNotFound"))
    return comment


async def delete_comment(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    *,
    handle: str,
    is_admin: bool,
) -> None:
    comment = await svc._visible_comment(
        feedback_id, comment_id, handle=handle, is_admin=is_admin
    )
    # The same predicate the read path fills `CommentOut.can_delete` with,
    # so the affordance and the permission cannot disagree.
    if not svc.may_delete_comment(comment, handle=handle, is_admin=is_admin):
        raise ForbiddenError(say("commentDeleteOwnOnly"))
    await svc._repo.soft_delete_comment(comment)


async def delete_feedback(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    *,
    handle: str,
    is_admin: bool,
) -> None:
    """软删一条反馈（连带它的评论）。

    `visible_row` 先过一遍**可见性**：看不见的东西回 404 而不是 403 —— 私人反馈
    存不存在本身就不该被一个看不见它的人问出来。可见之后再判「能不能删」，
    那是 403：这时候他已经知道这条存在了，再说「不存在」是另一句假话。
    """
    row = await svc.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    if not svc.may_delete_feedback(row, handle=handle, is_admin=is_admin):
        raise ForbiddenError(say("feedbackDeleteOwnOnly"))
    await svc._repo.soft_delete_feedback(row)


async def note(
    svc: pkg.FeedbackService, feedback_id: uuid.UUID, body: str, *, author_handle: str
) -> None:
    """Admin-only internal note. Append-only (see `FeedbackNote`)."""
    row = await svc._repo.get(feedback_id)
    if row is None:
        raise NotFoundError(say("feedbackNotFound"))
    await svc._repo.add_note(row.id, author_handle, body)
